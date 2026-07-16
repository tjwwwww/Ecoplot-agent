# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


OUT_DIR = Path("docs")


STYLE_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:docDefaults>
    <w:rPrDefault>
      <w:rPr><w:rFonts w:ascii="Microsoft YaHei" w:hAnsi="Microsoft YaHei" w:eastAsia="微软雅黑"/><w:sz w:val="22"/></w:rPr>
    </w:rPrDefault>
    <w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="360" w:lineRule="auto"/></w:pPr></w:pPrDefault>
  </w:docDefaults>
  <w:style w:type="paragraph" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>
  <w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:qFormat/><w:pPr><w:jc w:val="center"/><w:spacing w:after="240"/></w:pPr><w:rPr><w:b/><w:sz w:val="36"/><w:rFonts w:eastAsia="微软雅黑"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:spacing w:before="240" w:after="120"/><w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:b/><w:sz w:val="30"/><w:rFonts w:eastAsia="微软雅黑"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:spacing w:before="180" w:after="100"/><w:outlineLvl w:val="1"/></w:pPr><w:rPr><w:b/><w:sz w:val="26"/><w:rFonts w:eastAsia="微软雅黑"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="ListParagraph"><w:name w:val="List Paragraph"/><w:basedOn w:val="Normal"/><w:pPr><w:ind w:left="420"/></w:pPr></w:style>
</w:styles>"""

CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
  <Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>"""

RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>"""

DOC_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings" Target="settings.xml"/>
</Relationships>"""

SETTINGS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:settings xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:defaultTabStop w:val="420"/></w:settings>"""

APP = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"><Application>ForestryAgent</Application></Properties>"""


def paragraph(text: str, style: str = "Normal") -> str:
    return (
        f'<w:p><w:pPr><w:pStyle w:val="{style}"/></w:pPr>'
        f'<w:r><w:rPr><w:rFonts w:ascii="Microsoft YaHei" w:hAnsi="Microsoft YaHei" w:eastAsia="微软雅黑"/></w:rPr>'
        f'<w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>'
    )


def bullet(text: str) -> str:
    return paragraph("• " + text, "ListParagraph")


def document_xml(blocks: list[str]) -> str:
    section = (
        '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" w:header="708" w:footer="708" w:gutter="0"/>'
        "</w:sectPr>"
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" mc:Ignorable="">'
        f"<w:body>{''.join(blocks)}{section}</w:body></w:document>"
    )


def core_xml(title: str) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        f"<dc:title>{escape(title)}</dc:title><dc:creator>ForestryAgent</dc:creator>"
        f'<dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created>'
        f'<dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified>'
        "</cp:coreProperties>"
    )


def write_docx(path: Path, title: str, blocks: list[str]) -> None:
    with ZipFile(path, "w", ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        z.writestr("_rels/.rels", RELS)
        z.writestr("word/_rels/document.xml.rels", DOC_RELS)
        z.writestr("word/document.xml", document_xml(blocks))
        z.writestr("word/styles.xml", STYLE_XML)
        z.writestr("word/settings.xml", SETTINGS)
        z.writestr("docProps/core.xml", core_xml(title))
        z.writestr("docProps/app.xml", APP)


def build_proposal() -> list[str]:
    blocks = [paragraph("祁连山大样地野外调查智能体项目申报书", "Title")]
    blocks += [paragraph("一、项目背景", "Heading1")]
    blocks += [paragraph("祁连山森林生态系统具有重要的水源涵养、生物多样性保护和生态安全屏障功能。大样地长期积累了乔木、灌木、枯立木、地形、气候等多源观测数据，但传统分析方式依赖人工检索、手工统计和固定报表，难以快速响应野外调查、科学分析和管理决策中的多样化问题。")]
    blocks += [paragraph("本项目拟建设面向祁连山大样地的野外调查专家智能体，使其能够理解自然语言问题，自动调用数据、统计、制图和报告工具，形成有证据、有边界、可追溯的专业回答。")]
    blocks += [paragraph("二、建设目标", "Heading1")]
    for item in [
        "构建面向样地业务的林业本体和知识注册表，统一样方、单木、树种、灌木、枯立木、地形、气候和指标语义。",
        "建设智能体工具编排体系，使智能体能够根据问题复杂度自主选择直接回答、数据查询、统计分析、图表生成或报告输出。",
        "打通森林调查数据、地形因子、气候因子与可视化工具，支撑树种分布、样方质量、环境梯度、外业核查等场景。",
        "形成可用于项目申报、领导汇报、外业调查和科研分析的智能化辅助平台。",
    ]:
        blocks.append(bullet(item))
    blocks += [paragraph("三、技术方案", "Heading1")]
    blocks += [paragraph("系统采用“本体语义层 + 能力工具层 + 业务工作流层 + 专家回答层”的总体架构。智能体并非固定报告生成器，而是一个能够围绕林业样地业务进行自然语言交互、数据分析和证据解释的专业智能体。")]
    blocks += [paragraph("1. 本体语义层", "Heading2")]
    blocks += [paragraph("本体用于描述业务对象、对象关系、指标变量、字段映射和解释边界。它帮助智能体理解“青海云杉是树种”“海拔是地形因子”“胸径和树高是单木观测指标”“单木可通过 tree_id 与地形数据关联”等业务知识。")]
    blocks += [paragraph("2. 工具体系分层", "Heading2")]
    blocks += [paragraph("基础工具面向具体数据和确定性计算，例如查询样方记录、查询单木地形、计算林分密度、计算多样性、计算 Hegyi 竞争指数和生成基础图。")]
    blocks += [paragraph("能力工具面向通用能力，会关联和组合基础工具，也可以直接访问标准化数据接口，完成语义解析、数据发现、通用查询、分组统计、关系分析、图表生成和证据整理。")]
    blocks += [paragraph("业务工作流面向完整业务场景，通常包含多个能力工具和必要的基础工具，例如样方质量诊断、树种生境分析、海拔梯度影响评估、外业复测任务单和领导汇报报告。")]
    blocks += [paragraph("3. 智能体编排流程", "Heading2")]
    for item in [
        "快速判断问题复杂度：区分普通解释、简单查询、分析判断和复杂产出。",
        "语义理解：识别用户问题中的对象、指标、因子、范围和操作。",
        "数据检查：确认数据库是否有相关数据、字段、关联键、覆盖范围和缺失情况。",
        "工具规划：决定需要查询什么、统计什么、是否生成图表或报告。",
        "工具执行：调用查询、统计、制图和工作流工具，并记录过程。",
        "专家回答：输出证据、解释、边界和建议，避免把工具未命中误判为数据库没有。",
    ]:
        blocks.append(bullet(item))
    blocks += [paragraph("四、创新点", "Heading1")]
    for item in [
        "本体增强的林业智能体：以样地本体作为业务语义地图，使智能体理解林业调查对象、指标和关系。",
        "通用能力工具编排：不是为每个问题写死工具，而是通过通用查询、统计、制图和证据工具覆盖多数业务问题。",
        "多源数据融合分析：融合乔木、灌木、枯立木、地形、气候等数据，支持环境因子与林分结构联合分析。",
        "证据链回答机制：回答不仅给结论，还展示数据来源、工具过程、样本量、字段和边界。",
        "面向野外调查的任务闭环：可从问题理解延伸到候选样方、候选单木、复测清单和领导汇报。",
    ]:
        blocks.append(bullet(item))
    blocks += [paragraph("五、应用场景", "Heading1")]
    for item in [
        "样方质量诊断：分析某样方林分密度、树种组成、径级结构、形态特征和相对关注信号。",
        "树种分布与生境分析：分析青海云杉、祁连圆柏、红桦、乌柳等树种在样地中的空间、地形和气候背景。",
        "环境梯度影响评估：分析海拔、坡度、坡向、温度、降水等因子与胸径、树高、株数、密度的关系。",
        "外业复测辅助：生成重点样方、候选单木、现场观察项和记录要求。",
        "管理决策汇报：根据用户要求自动生成文字、表格、图表和结构化报告。",
    ]:
        blocks.append(bullet(item))
    blocks += [paragraph("六、预期效果", "Heading1")]
    for item in [
        "提升数据利用效率：从人工查询表格转变为自然语言交互式分析。",
        "提升回答可靠性：先检查数据覆盖，再调用工具分析，减少凭空推断。",
        "提升外业效率：帮助调查人员快速定位重点样方、重点树种和待核实对象。",
        "提升成果表达能力：支持面向领导、科研人员和外业人员的不同粒度输出。",
        "形成可扩展平台：后续可继续接入遥感、土壤、病虫害、长期监测和模型预测数据。",
    ]:
        blocks.append(bullet(item))
    return blocks


def build_technical_plan() -> list[str]:
    blocks = [paragraph("祁连山大样地野外调查智能体技术方案说明文档", "Title")]
    blocks += [paragraph("一、系统定位与设计原则", "Heading1")]
    blocks += [paragraph("本系统定位为面向祁连山大样地的林业调查与分析智能体。其核心能力不是简单问答，而是围绕样地调查数据、地形数据、气候数据和林业知识开展自然语言理解、数据检索、统计分析、图表生成和专家解释。")]
    for item in [
        "用户不需要按固定格式提问，智能体内部负责将自然语言还原为对象、指标/因子、范围和操作。",
        "简单问题直接回答，复杂问题进入数据检查和工具编排流程。",
        "本体是业务语义地图，工具是可执行能力，智能体负责规划和解释。",
        "工具结果优先于猜测，数据不足时说明边界和可替代分析路径。",
    ]:
        blocks.append(bullet(item))
    blocks += [paragraph("二、模型架构", "Heading1")]
    blocks += [paragraph("系统采用分层智能体架构：快速分流层、语义理解层、数据检查层、工具规划与执行层、专家回答层。")]
    for title, text in [
        ("1. 快速分流层", "判断用户问题是否需要工具，并区分普通解释、简单数据查询、分析判断和复杂产出。该层避免所有问题都走完整工具链，提高交互效率。"),
        ("2. 语义理解层", "将用户自由表达转换为内部任务结构，例如 intent、target、factor、metrics、scope、output。该层依赖本体、同义词、字段映射和上下文记忆。"),
        ("3. 数据检查层", "确认数据是否存在、位于哪些表、可通过哪些键关联、覆盖范围如何、是否存在缺失。该层用于防止把“工具未命中”误判为“数据库没有”。"),
        ("4. 工具规划与执行层", "根据任务结构规划查询、统计、制图或工作流执行，并记录工具调用过程、参数、结果摘要和产物文件。"),
        ("5. 专家回答层", "根据工具结果组织中文回答，包含正式数据结果、趋势解释、图表说明、生态学解释、数据边界和外业建议。"),
    ]:
        blocks += [paragraph(title, "Heading2"), paragraph(text)]
    blocks += [paragraph("三、算法设计", "Heading1")]
    blocks += [paragraph("1. 问题语义解析算法", "Heading2")]
    for item in ["输入：用户自然语言、会话历史、页面上下文。", "处理：识别对象类型、指标变量、环境因子、空间范围、时间范围、输出需求。", "输出：结构化任务对象，例如环境梯度分析、样方诊断、树种分布分析、报告生成等。"]:
        blocks.append(bullet(item))
    blocks += [paragraph("2. 数据可用性检查算法", "Heading2")]
    for item in ["检查目标对象是否存在，例如树种、样方、单木、气象站。", "检查变量是否存在，例如胸径、树高、海拔、坡度、温度、降水。", "检查关联关系是否可用，例如 tree_id、subplot_id、station_id、observation_date。", "输出数据覆盖量、样本量、时间范围、缺失情况和可分析性。"]:
        blocks.append(bullet(item))
    blocks += [paragraph("3. 通用数据查询算法", "Heading2")]
    blocks += [paragraph("通过统一查询接口，根据 filters、fields、joins、scope、limit 等参数获取结构化数据。该接口应支持乔木、灌木、枯立木、地形、气候等多类数据。")]
    blocks += [paragraph("4. 通用分析算法", "Heading2")]
    for item in ["分组统计：按树种、样方、海拔带、坡度等级、年份或月份统计。", "关系分析：分析海拔、坡度、气候因子与胸径、树高、株数、密度等指标的关系。", "对比分析：比较不同样方、不同树种、不同环境梯度之间的差异。", "异常筛查：识别高密度、极端高径比、低多样性、特殊生境集中分布等相对关注信号。"]:
        blocks.append(bullet(item))
    blocks += [paragraph("5. 图表生成算法", "Heading2")]
    blocks += [paragraph("根据分析结果自动选择图表类型，包括柱状图、箱线图、散点图、折线图、空间分布图和热力图。图表工具应支持 x、y、group_by、color_by、filter、chart_type 等通用参数。")]
    blocks += [paragraph("四、数据集说明", "Heading1")]
    for item in [
        "乔木调查数据：包含样方编号、单木编号、树种、胸径、树高、冠幅、枝下高、健康状态等。",
        "灌木调查数据：包含样方编号、灌木种类、个体数、高度、盖度等。",
        "枯立木/倒木数据：包含样方编号、树种、数量、状态和备注等。",
        "地形数据：包含单木或样方相关的海拔、坡度、坡向、坡位等。",
        "气候数据：包含气象站信息、1956—2024 年逐日观测数据，以及逐月、逐年摘要数据。",
        "本体与知识注册表：包含业务对象、指标变量、公式、规则、字段映射和解释边界。",
    ]:
        blocks.append(bullet(item))
    blocks += [paragraph("五、工具体系设计", "Heading1")]
    blocks += [paragraph("1. 基础工具", "Heading2"), paragraph("基础工具执行具体的数据查询、确定性计算或基础制图。例如查询单木地形、计算林分密度、计算多样性、生成某样方径级图。")]
    blocks += [paragraph("2. 能力工具", "Heading2"), paragraph("能力工具是系统最关键的一层。它们会关联基础工具，也可以直接调用标准化数据接口，用于完成语义解析、数据发现、通用查询、统计分析、图表生成和证据整理。能力工具不是窄业务工具，而是支撑多数问题的通用能力。")]
    blocks += [paragraph("3. 业务工作流", "Heading2"), paragraph("业务工作流面向完整业务场景，通常包含多个能力工具，并在必要时调用基础工具。例如“海拔梯度影响评估”工作流会调用语义解析、数据检查、查询、分组统计、图表生成和专家回答。")]
    blocks += [paragraph("六、典型技术路线图", "Heading1")]
    for item in [
        "用户提出问题：例如“海拔对青海云杉有影响吗？”",
        "快速分流：判定为分析判断类问题。",
        "语义理解：识别目标为青海云杉，因子为海拔，指标为株数、胸径、树高，范围为全样地。",
        "数据检查：确认 tree_observations 与 topography_observations 可通过 tree_id 关联。",
        "数据查询：获取青海云杉单木、样方、胸径、树高、海拔等字段。",
        "分析计算：按海拔带统计株数、平均胸径、平均树高，并计算趋势。",
        "图表生成：生成海拔分组箱线图或散点图。",
        "专家回答：给出数据结果、趋势解释、生态学解释、边界和外业建议。",
    ]:
        blocks.append(bullet(item))
    blocks += [paragraph("七、准确性保障机制", "Heading1")]
    for item in ["先检查数据，再生成结论。", "工具未命中时进行替代路径验证。", "回答中明确区分原始观测、确定性计算、统计分析、经验解释和待现场核实内容。", "所有图表和报告保留工具调用记录、数据字段、样本量和产物文件路径。", "多轮对话中保留上下文，但当前用户问题优先于历史焦点。"]:
        blocks.append(bullet(item))
    blocks += [paragraph("八、研发实施建议", "Heading1")]
    for item in ["重构 agent.py，使其保留对话入口、流式输出、会话记忆和工具事件展示。", "新增 forest_intelligence_core.py，承载语义理解、数据发现、通用查询、分析和制图能力。", "将现有 forestry_spatial_tools.py、forestry_visualization_engine.py 和 domain_analysis_engine.py 收编为基础工具与能力工具。", "完善本体与数据库字段映射，保证智能体能从概念定位到真实字段。", "前端展示最终答案、工具过程、图表文件和证据摘要，支持用户展开查看。"]:
        blocks.append(bullet(item))
    return blocks


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    files = [
        (OUT_DIR / "ForestryAgent_Project_Proposal.docx", "祁连山林业智能体项目申报书", build_proposal()),
        (OUT_DIR / "ForestryAgent_Technical_Plan.docx", "祁连山林业智能体技术方案说明文档", build_technical_plan()),
    ]
    for path, title, blocks in files:
        write_docx(path, title, blocks)
        print(path.resolve())


if __name__ == "__main__":
    main()
