# -*- coding: utf-8 -*-
"""
==============================================================================
神经-符号公式统一执行调度引擎 (Neuro-Symbolic Formula Execution Engine)
==============================================================================
本引擎是林业知识图谱架构从“死数据”向“可驱动计算引擎”演进的核心组件。
执行机制分两轨运行：
【轨道一：算子显式绑定 (Track 1 - Script Operator Binding)】
  针对二维矩阵查表(F_VOL_2D_TABLE)、全样方香农指数迭代(F_SHANNON_INDEX)、间伐推演等复杂模型，
  根据图谱中 :IMPLEMENTS 关系或 YAML 配置中的 tool_binding 显式调用具体 Python 算子方法。

【轨道二：声明式符号解析 (Track 2 - Declarative Symbolic Evaluation)】
  针对测树学常规代数方程 (如 F_STAND_DENSITY, F_HEIGHT_DIAMETER_RATIO, F_CROWN_WIDTH_MEAN)，
  根据公式的 symbol_mappings 将传入的数据上下文精准映射至公式符号，动态代入表达式求值。
==============================================================================
"""

import os
import sys
import yaml
import json
import math
import ast
import operator as op

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

# 安全 AST 运算操作符表，防范 eval 代码注入风险
SAFE_OPERATORS = {
    ast.Add: op.add,
    ast.Sub: op.sub,
    ast.Mult: op.mul,
    ast.Div: op.truediv,
    ast.Pow: op.pow,
    ast.USub: op.neg,
    ast.UAdd: op.pos,
}

SAFE_FUNCTIONS = {
    'sqrt': math.sqrt,
    'log': math.log,
    'ln': math.log,
    'exp': math.exp,
    'pi': math.pi,
    'e': math.e,
}

def safe_eval_ast(node, variables):
    """
    通过 AST 递归安全求解数学表达式
    """
    if isinstance(node, ast.Num):
        return node.n
    elif isinstance(node, ast.Constant): # Python 3.8+
        return node.value
    elif isinstance(node, ast.Name):
        if node.id in variables:
            return variables[node.id]
        elif node.id in SAFE_FUNCTIONS:
            return SAFE_FUNCTIONS[node.id]
        else:
            raise ValueError(f"公式解析出错: 未知或未绑定的符号 '{node.id}'")
    elif isinstance(node, ast.BinOp):
        left = safe_eval_ast(node.left, variables)
        right = safe_eval_ast(node.right, variables)
        op_type = type(node.op)
        if op_type in SAFE_OPERATORS:
            return SAFE_OPERATORS[op_type](left, right)
        else:
            raise ValueError(f"不支持的二元操作符: {op_type}")
    elif isinstance(node, ast.UnaryOp):
        operand = safe_eval_ast(node.operand, variables)
        op_type = type(node.op)
        if op_type in SAFE_OPERATORS:
            return SAFE_OPERATORS[op_type](operand)
        else:
            raise ValueError(f"不支持的一元操作符: {op_type}")
    elif isinstance(node, ast.Call):
        func_name = node.func.id if isinstance(node.func, ast.Name) else None
        if func_name in SAFE_FUNCTIONS and callable(SAFE_FUNCTIONS[func_name]):
            args = [safe_eval_ast(arg, variables) for arg in node.args]
            return SAFE_FUNCTIONS[func_name](*args)
        else:
            raise ValueError(f"不支持的函数调用: {func_name}")
    else:
        raise ValueError(f"语法树节点解析不受支持: {type(node)}")

class NeuroSymbolicFormulaEngine:
    def __init__(self, registry_path=None):
        if registry_path is None:
            # 默认指向当前项目目录下的 ontology/forestry_knowledge_registry.yaml
            base_dir = os.path.dirname(os.path.abspath(__file__))
            registry_path = os.path.join(base_dir, "ontology", "forestry_knowledge_registry.yaml")
            
        self.registry_path = registry_path
        self.knowledge_map = {}
        self.load_registry()

    def load_registry(self):
        """加载配置中的所有公式与模型定义"""
        if not os.path.exists(self.registry_path):
            print(f"[报错] 找不到知识注册配置文件: {self.registry_path}")
            return
            
        with open(self.registry_path, "r", encoding="utf-8") as f:
            self.raw_data = yaml.safe_load(f) or {}
            
        # 公式执行器只加载 formulas 和 models；规则由 interpretive_rule_engine.py 独立管理。
        for category in ["formulas", "models"]:
            for item in self.raw_data.get(category, []):
                kid = item.get("knowledge_id")
                if kid:
                    self.knowledge_map[kid] = item

    def get_formula_definition(self, knowledge_id: str):
        return self.knowledge_map.get(knowledge_id)

    def execute_formula(self, knowledge_id: str, data_context: dict, force_track: str = None):
        """
        统一执行接口
        :param knowledge_id: 公式知识 ID，如 F_STAND_DENSITY, F_CROWN_WIDTH_MEAN
        :param data_context: 变量 ID/参数上下文，如 {'VAR_TOTAL_TREE_COUNT': 84, 'VAR_SUBPLOT_AREA': 0.04}
        :param force_track: 可强制指定 'Track1' (具体算子) 或 'Track2' (符号解析)
        """
        f_def = self.get_formula_definition(knowledge_id)
        if not f_def:
            return {"error": f"知识图谱中不存在公式定义: {knowledge_id}", "status": "FAILED"}

        expr = f_def.get("expression", "")
        symbol_mappings = f_def.get("symbol_mappings", {})
        tool_binding = f_def.get("tool_binding", "")
        name_cn = f_def.get("name_cn", "")

        # 判断运行轨道
        # 如果 force_track==Track1 或 (存在 tool_binding 且未强制 Track2 且表达式包含复杂函数如 lookup_matrix, ∑)
        use_track_1 = (force_track == "Track1") or (
            tool_binding and force_track != "Track2" and ("lookup_matrix" in expr or "∑" in expr or "sum(" in expr)
        )

        if use_track_1:
            return self._execute_track1_operator(f_def, data_context)
        else:
            return self._execute_track2_symbolic(f_def, data_context)

    def _execute_track2_symbolic(self, f_def: dict, data_context: dict):
        """
        轨道二：声明式符号解析与动态代数求解
        """
        knowledge_id = f_def["knowledge_id"]
        expr = f_def.get("expression", "")
        symbol_mappings = f_def.get("symbol_mappings", {})

        if "=" not in expr:
            return {
                "knowledge_id": knowledge_id,
                "status": "FAILED",
                "error": f"表达式格式不符合标准的等式 (lhs = rhs): {expr}"
            }

        lhs_str, rhs_str = [part.strip() for part in expr.split("=", 1)]
        output_symbol = lhs_str
        output_target_id = symbol_mappings.get(output_symbol, f_def.get("produces_indicator", ""))

        # 构造符号解析与代入追溯表
        symbol_binding_trace = {}
        eval_variables = {}
        missing_symbols = []

        # 遍历公式右侧需要使用的所有数学字母，并在 symbol_mappings 中寻找对应的实体变量 ID
        for sym, target_var_id in symbol_mappings.items():
            if sym == output_symbol:
                continue
            # 判断右侧字符串是否包含了该符号 (或简单全量匹配映射表中的变量)
            if sym in rhs_str or target_var_id in data_context:
                if target_var_id in data_context:
                    val = float(data_context[target_var_id])
                    symbol_binding_trace[sym] = {"variable_id": target_var_id, "value": val}
                    eval_variables[sym] = val
                elif sym in data_context: # 也支持直接传符号作为 key
                    val = float(data_context[sym])
                    symbol_binding_trace[sym] = {"variable_id": target_var_id, "value": val}
                    eval_variables[sym] = val
                else:
                    missing_symbols.append(f"{sym} (绑定变量: {target_var_id})")

        if missing_symbols:
            return {
                "knowledge_id": knowledge_id,
                "name_cn": f_def.get("name_cn", ""),
                "track_used": "Track 2: Declarative Symbolic Evaluation (声明式符号解析)",
                "status": "FAILED",
                "error": f"缺少计算所需的输入参数: {', '.join(missing_symbols)}。传入上下文为: {list(data_context.keys())}"
            }

        # 构建代入具体数值后的公式调试字符串
        resolved_math_string = rhs_str
        # 为避免短字母（如 D, n）错误替换长字母部分，按字母长度从长到短做字符串展示替换
        sorted_syms = sorted(eval_variables.keys(), key=lambda k: len(k), reverse=True)
        for s in sorted_syms:
            resolved_math_string = resolved_math_string.replace(s, str(eval_variables[s]))

        # 使用 AST 语法树安全解析计算
        try:
            tree = ast.parse(rhs_str, mode='eval')
            computed_value = safe_eval_ast(tree.body, eval_variables)
            computed_value = round(float(computed_value), 4)
            return {
                "knowledge_id": knowledge_id,
                "name_cn": f_def.get("name_cn", ""),
                "track_used": "Track 2: Declarative Symbolic Evaluation (声明式符号动态求值)",
                "formula_expression": expr,
                "symbol_binding_trace": symbol_binding_trace,
                "resolved_math_string": resolved_math_string,
                "output_symbol": output_symbol,
                "output_target_id": output_target_id,
                "computed_value": computed_value,
                "status": "SUCCESS"
            }
        except Exception as e:
            return {
                "knowledge_id": knowledge_id,
                "name_cn": f_def.get("name_cn", ""),
                "track_used": "Track 2: Declarative Symbolic Evaluation (声明式符号动态求值)",
                "formula_expression": expr,
                "status": "FAILED",
                "error": f"AST 语法树求值失败: {str(e)} (尝试代入的公式: {resolved_math_string})"
            }

    def _execute_track1_operator(self, f_def: dict, data_context: dict):
        """
        轨道一：显式算子绑定调用 (如调用 forestry_spatial_tools.py 里的具体 Python 函数)
        """
        knowledge_id = f_def["knowledge_id"]
        tool_name = f_def.get("tool_binding", "")
        
        # 尝试动态加载或调用算子模块
        # 例如若需要计算样方基础指标，则从 forestry_spatial_tools 导入并调用
        try:
            import forestry_spatial_tools as fst
            func_map = {
                "calculate_stand_structure_metrics": fst.tool_calc_stand_structure_metrics,
                "calculate_tree_morphology_metrics": fst.tool_calc_tree_morphology_metrics,
                "calculate_species_diversity_metrics": fst.tool_calc_species_diversity_metrics,
                "calculate_volume_metrics": fst.tool_calc_volume_metrics,
                "calculate_deadwood_metrics": fst.tool_calc_deadwood_metrics,
                "calculate_shrub_metrics": fst.tool_calc_shrub_metrics,
                "calculate_hegyi_competition": fst.tool_calc_hegyi_competition,
                "simulate_thinning_prescription": fst.tool_simulate_thinning_prescription,
            }
            if tool_name in func_map:
                subplot_id = data_context.get("subplot_id", "2816") # 默认测试样方
                # 兼容不同算子参数
                if tool_name == "calculate_hegyi_competition":
                    raw_res = func_map[tool_name](subplot_id, data_context.get("target_tree_id"), data_context.get("radius_m", 6.0))
                elif tool_name == "simulate_thinning_prescription":
                    raw_res = func_map[tool_name](subplot_id, data_context.get("target_thinning_pct", 20.0), data_context.get("method", "low_thinning"))
                elif tool_name == "calculate_species_diversity_metrics":
                    raw_res = func_map[tool_name](subplot_id, data_context.get("survey_event_id", "EVENT_2023"), data_context.get("unknown_taxon_policy", "exclude"))
                else:
                    raw_res = func_map[tool_name](subplot_id)
                return {
                    "knowledge_id": knowledge_id,
                    "name_cn": f_def.get("name_cn", ""),
                    "track_used": f"Track 1: Script Operator Binding (调用实体 Python 函数 {tool_name})",
                    "formula_expression": f_def.get("expression", ""),
                    "input_context": data_context,
                    "operator_result": json.loads(raw_res) if isinstance(raw_res, str) and raw_res.startswith("{") else raw_res,
                    "status": "SUCCESS"
                }
            else:
                return {
                    "knowledge_id": knowledge_id,
                    "status": "FAILED",
                    "error": f"声明的绑定算子 {tool_name} 未在 forestry_spatial_tools 中注册执行函数。"
                }
        except Exception as e:
            return {
                "knowledge_id": knowledge_id,
                "status": "FAILED",
                "error": f"调用具体 Python 算子脚本抛出异常: {str(e)}"
            }

    
def run_demonstration():
    print("==============================================================================")
    print(" 🌲 神经-符号公式统一执行引擎 (Neuro-Symbolic Formula Execution Engine) 演示")
    print("==============================================================================\n")
    
    engine = NeuroSymbolicFormulaEngine()
    
    print("【测试 1: 轨道二符号解析 - 计算单木高径比方程 F_HEIGHT_DIAMETER_RATIO】")
    # 输入单木树高 24.5 米，胸径 32.0 cm
    context_1 = {
        "VAR_TREE_HEIGHT": 24.5,
        "VAR_TREE_DBH": 32.0
    }
    res_1 = engine.execute_formula("F_HEIGHT_DIAMETER_RATIO", context_1)
    print(json.dumps(res_1, indent=2, ensure_ascii=False))
    print("-" * 75)

    print("\n【测试 2: 轨道二符号解析 - 计算林分密度方程 F_STAND_DENSITY】")
    # 样方内乔木总株数 N=84，样方面积 Area=0.04 hm²
    context_2 = {
        "VAR_TOTAL_TREE_COUNT": 84,
        "VAR_SUBPLOT_AREA": 0.04
    }
    # 强制指定以 Track2 符号解析求值
    res_2 = engine.execute_formula("F_STAND_DENSITY", context_2, force_track="Track2")
    print(json.dumps(res_2, indent=2, ensure_ascii=False))
    print("-" * 75)

    print("\n【测试 3: 轨道二符号解析 - 计算平均冠幅方程 F_CROWN_WIDTH_MEAN (本轮细分新增)】")
    # 某大树东西冠幅 W_EW=6.8 米，南北冠幅 W_NS=7.4 米
    context_3 = {
        "VAR_CROWN_WIDTH_EW": 6.8,
        "VAR_CROWN_WIDTH_NS": 7.4
    }
    res_3 = engine.execute_formula("F_CROWN_WIDTH_MEAN", context_3)
    print(json.dumps(res_3, indent=2, ensure_ascii=False))
    print("-" * 75)

    print("\n【测试 4: 轨道一算子调用 - 针对全样方的测树算子脚本显式驱动】")
    # 调用实体 Python 算子函数求 2816 样方全量指标
    context_4 = {"subplot_id": "2816"}
    res_4 = engine.execute_formula("F_STAND_DENSITY", context_4, force_track="Track1")
    print(json.dumps(res_4, indent=2, ensure_ascii=False))
    print("==============================================================================\n")

if __name__ == "__main__":
    run_demonstration()
