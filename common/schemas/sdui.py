"""
Server-Driven UI (SDUI) Schema Definitions
服务端驱动 UI 数据模型定义

基于 Microsoft Adaptive Cards 1.6 标准
允许插件开发者使用 Python 对象构建 UI，而不是手写 JSON

设计理念：
- 插件开发者定义业务逻辑和 UI 布局
- Core (Tier 2) 将 UI Schema 序列化为 JSON
- Client (Tier 3) 使用通用渲染引擎解析并渲染
- 结果：新增插件无需更新客户端 App
"""

from typing import Optional, List, Dict, Any, Union, Literal, TYPE_CHECKING
from pydantic import ConfigDict
from pydantic import BaseModel, Field
from enum import Enum

if TYPE_CHECKING:
    # 用于类型提示，避免循环引用
    pass


# ============================================================================
# Adaptive Cards 基础类型定义
# ============================================================================

class ActionType(str, Enum):
    """操作类型"""
    SUBMIT = "Action.Submit"
    OPEN_URL = "Action.OpenUrl"
    SHOW_CARD = "Action.ShowCard"
    TOGGLE_VISIBILITY = "Action.ToggleVisibility"


class ElementType(str, Enum):
    """元素类型"""
    TEXT_BLOCK = "TextBlock"
    IMAGE = "Image"
    MEDIA = "Media"
    RICH_TEXT_BLOCK = "RichTextBlock"
    FACT_SET = "FactSet"
    IMAGE_SET = "ImageSet"
    INPUT_TEXT = "Input.Text"
    INPUT_NUMBER = "Input.Number"
    INPUT_DATE = "Input.Date"
    INPUT_TIME = "Input.Time"
    INPUT_TOGGLE = "Input.Toggle"
    INPUT_CHOICE_SET = "Input.ChoiceSet"
    INPUT_CHOICE = "Input.Choice"
    CONTAINER = "Container"
    COLUMN_SET = "ColumnSet"
    COLUMN = "Column"
    ADAPTIVE_CARD = "AdaptiveCard"


class HorizontalAlignment(str, Enum):
    """水平对齐方式"""
    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"


class VerticalAlignment(str, Enum):
    """垂直对齐方式"""
    TOP = "top"
    CENTER = "center"
    BOTTOM = "bottom"


class FontSize(str, Enum):
    """字体大小"""
    DEFAULT = "default"
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"
    EXTRA_LARGE = "extraLarge"


class FontWeight(str, Enum):
    """字体粗细"""
    DEFAULT = "default"
    LIGHTER = "lighter"
    BOLDER = "bolder"


class Color(str, Enum):
    """颜色"""
    DEFAULT = "default"
    DARK = "dark"
    LIGHT = "light"
    ACCENT = "accent"
    GOOD = "good"
    WARNING = "warning"
    ATTENTION = "attention"


class Spacing(str, Enum):
    """间距"""
    DEFAULT = "default"
    NONE = "none"
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"
    EXTRA_LARGE = "extraLarge"
    PADDING = "padding"


# ============================================================================
# 基础元素模型
# ============================================================================

class BaseElement(BaseModel):
    """基础元素模型"""
    type: str
    id: Optional[str] = None
    spacing: Optional[Spacing] = None
    separator: Optional[bool] = None
    is_visible: Optional[bool] = None


class TextBlock(BaseElement):
    """文本块"""
    type: Literal[ElementType.TEXT_BLOCK.value] = Field(default=ElementType.TEXT_BLOCK.value)
    text: str = Field(description="文本内容")
    color: Optional[Color] = None
    font_type: Optional[str] = None
    size: Optional[FontSize] = None
    weight: Optional[FontWeight] = None
    wrap: Optional[bool] = Field(default=True, description="是否自动换行")
    max_lines: Optional[int] = None
    horizontal_alignment: Optional[HorizontalAlignment] = None


class Image(BaseElement):
    """图片"""
    type: Literal[ElementType.IMAGE.value] = Field(default=ElementType.IMAGE.value)
    url: str = Field(description="图片 URL")
    alt_text: Optional[str] = None
    horizontal_alignment: Optional[HorizontalAlignment] = None
    size: Optional[str] = None  # "auto", "stretch", "small", "medium", "large"
    width: Optional[str] = None
    height: Optional[str] = None
    style: Optional[str] = None  # "default", "person"


class Action(BaseModel):
    """操作按钮"""
    type: ActionType
    title: str = Field(description="按钮标题")
    id: Optional[str] = None
    data: Optional[Dict[str, Any]] = Field(default=None, description="提交的数据")


class SubmitAction(Action):
    """提交操作"""
    type: Literal[ActionType.SUBMIT] = Field(default=ActionType.SUBMIT)


class OpenUrlAction(Action):
    """打开 URL 操作"""
    type: Literal[ActionType.OPEN_URL] = Field(default=ActionType.OPEN_URL)
    url: str = Field(description="要打开的 URL")


class InputText(BaseElement):
    """文本输入框"""
    type: Literal[ElementType.INPUT_TEXT.value] = Field(default=ElementType.INPUT_TEXT.value)
    id: str = Field(description="输入框 ID，用于提交数据")
    placeholder: Optional[str] = None
    value: Optional[str] = None
    is_multiline: Optional[bool] = None
    max_length: Optional[int] = None
    style: Optional[str] = None  # "text", "email", "tel", "url"


class InputNumber(BaseElement):
    """数字输入框"""
    type: Literal[ElementType.INPUT_NUMBER.value] = Field(default=ElementType.INPUT_NUMBER.value)
    id: str = Field(description="输入框 ID")
    placeholder: Optional[str] = None
    value: Optional[float] = None
    min: Optional[float] = None
    max: Optional[float] = None


class InputToggle(BaseElement):
    """开关"""
    type: Literal[ElementType.INPUT_TOGGLE.value] = Field(default=ElementType.INPUT_TOGGLE.value)
    id: str = Field(description="开关 ID")
    title: str = Field(description="开关标题")
    value: Optional[str] = None  # "true" 或 "false"
    value_on: Optional[str] = Field(default="true")
    value_off: Optional[str] = Field(default="false")


class InputChoice(BaseModel):
    """选择项"""
    title: str
    value: str


class InputChoiceSet(BaseElement):
    """选择集（单选或多选）"""
    type: Literal[ElementType.INPUT_CHOICE_SET.value] = Field(default=ElementType.INPUT_CHOICE_SET.value)
    id: str = Field(description="选择集 ID")
    choices: List[InputChoice] = Field(description="选项列表")
    is_multiselect: Optional[bool] = Field(default=False, description="是否多选")
    style: Optional[str] = None  # "compact", "expanded"


class Container(BaseElement):
    """容器"""
    type: Literal[ElementType.CONTAINER.value] = Field(default=ElementType.CONTAINER.value)
    items: List[Union[TextBlock, Image, InputText, InputNumber, InputToggle, InputChoiceSet, "Container"]] = Field(
        description="容器内的元素列表"
    )
    style: Optional[str] = None  # "default", "emphasis"
    vertical_content_alignment: Optional[VerticalAlignment] = None
    background_image: Optional[str] = None
    bleed: Optional[bool] = None


class Column(BaseModel):
    """列"""
    type: Literal[ElementType.COLUMN.value] = Field(default=ElementType.COLUMN.value)
    items: List[Union[TextBlock, Image, Container]] = Field(description="列内的元素")
    width: Optional[str] = None  # "auto", "stretch", 或像素值
    spacing: Optional[Spacing] = None
    vertical_content_alignment: Optional[VerticalAlignment] = None


class ColumnSet(BaseElement):
    """列集（多列布局）"""
    type: Literal[ElementType.COLUMN_SET.value] = Field(default=ElementType.COLUMN_SET.value)
    columns: List[Column] = Field(description="列列表")


# ============================================================================
# Adaptive Card 主模型
# ============================================================================

class AdaptiveCard(BaseModel):
    """
    Adaptive Card 主模型
    
    这是插件开发者需要构建的 UI 结构
    示例：
        card = AdaptiveCard(
            version="1.6",
            body=[
                TextBlock(text="当前股价: $120", size=FontSize.LARGE),
                TextBlock(text="苹果公司 (AAPL)", color=Color.ACCENT),
                SDUIChart(chart_type="line", title="股价趋势", data=[...])
            ],
            actions=[
                SubmitAction(title="买入", id="buy_action", data={"action": "buy", "symbol": "AAPL"}),
                SubmitAction(title="卖出", id="sell_action", data={"action": "sell"})
            ]
        )
    """
    type: Literal[ElementType.ADAPTIVE_CARD.value] = Field(default=ElementType.ADAPTIVE_CARD.value)
    version: str = Field(default="1.6", description="Adaptive Cards 版本")
    body: List[Union[
        TextBlock, Image, Container, ColumnSet,
        InputText, InputNumber, InputToggle, InputChoiceSet,
        "SDUIProgressBar", "SDUIChart", "SDUIList", "SDUIButton"  # Jachin 扩展组件
    ]] = Field(description="卡片主体内容")
    actions: Optional[List[Union[SubmitAction, OpenUrlAction]]] = Field(
        default=None,
        description="操作按钮列表"
    )
    schema_: Optional[str] = Field(
        default=None,
        alias="$schema",
        description="JSON Schema URL（可选）"
    )

    model_config = ConfigDict(
        use_enum_values=True,
        populate_by_name=True,
        # 注意：json_encoders 在 Pydantic v2 中已废弃，枚举值会自动序列化
    )

    def to_json(self) -> str:
        """转换为 JSON 字符串（用于 ui_render_schema 字段）"""
        import json
        return json.dumps(self.model_dump(exclude_none=True, by_alias=True), ensure_ascii=False)


# ============================================================================
# 辅助函数：快速构建常用 UI 组件
# ============================================================================

def create_simple_card(
    title: str,
    content: str,
    actions: Optional[List[Action]] = None
) -> AdaptiveCard:
    """
    快速创建简单卡片
    
    示例：
        card = create_simple_card(
            title="股票查询结果",
            content="当前股价: $120",
            actions=[SubmitAction(title="买入", id="buy")]
        )
    """
    body = [
        TextBlock(text=title, size=FontSize.LARGE, weight=FontWeight.BOLDER),
        TextBlock(text=content, wrap=True)
    ]
    return AdaptiveCard(body=body, actions=actions or [])


def create_form_card(
    title: str,
    fields: List[Union[InputText, InputNumber, InputToggle, InputChoiceSet]],
    submit_action: SubmitAction
) -> AdaptiveCard:
    """
    快速创建表单卡片
    
    示例：
        card = create_form_card(
            title="买入股票",
            fields=[
                InputText(id="symbol", placeholder="股票代码", value="AAPL"),
                InputNumber(id="quantity", placeholder="数量", value=100)
            ],
            submit_action=SubmitAction(title="确认买入", id="confirm_buy")
        )
    """
    body = [TextBlock(text=title, size=FontSize.LARGE, weight=FontWeight.BOLDER)]
    body.extend(fields)
    return AdaptiveCard(body=body, actions=[submit_action])


def create_list_card(
    title: str,
    items: List[str],
    actions: Optional[List[Action]] = None
) -> AdaptiveCard:
    """
    快速创建列表卡片
    
    示例：
        card = create_list_card(
            title="股票列表",
            items=["AAPL - 苹果公司", "MSFT - 微软公司", "GOOGL - 谷歌公司"]
        )
    """
    body = [TextBlock(text=title, size=FontSize.LARGE, weight=FontWeight.BOLDER)]
    for item in items:
        body.append(TextBlock(text=f"• {item}", spacing=Spacing.SMALL))
    return AdaptiveCard(body=body, actions=actions or [])


# ============================================================================
# Jachin 扩展组件（基于 Adaptive Cards，但提供更高级的抽象）
# ============================================================================

class SDUIComponent(BaseElement):
    """SDUI 组件基类"""
    type: str = Field(description="组件类型")


class SDUIProgressBar(SDUIComponent):
    """进度条组件（用于文件传输、下载等）"""
    type: Literal["SDUI.ProgressBar"] = Field(default="SDUI.ProgressBar")
    title: Optional[str] = Field(default=None, description="进度条标题")
    value: float = Field(ge=0.0, le=100.0, description="当前进度值（0-100）")
    max_value: float = Field(default=100.0, description="最大值")
    show_percentage: Optional[bool] = Field(default=True, description="是否显示百分比")
    status_text: Optional[str] = Field(default=None, description="状态文本（如 '下载中...'）")
    color: Optional[Color] = Field(default=None, description="进度条颜色")


class SDUIChart(SDUIComponent):
    """图表组件（用于性能监控、数据可视化等）"""
    type: Literal["SDUI.Chart"] = Field(default="SDUI.Chart")
    chart_type: str = Field(description="图表类型：'line', 'bar', 'pie', 'area'")
    title: Optional[str] = Field(default=None, description="图表标题")
    data: List[Dict[str, Any]] = Field(description="图表数据")
    x_axis_label: Optional[str] = Field(default=None, description="X轴标签")
    y_axis_label: Optional[str] = Field(default=None, description="Y轴标签")
    show_legend: Optional[bool] = Field(default=True, description="是否显示图例")
    height: Optional[str] = Field(default="200px", description="图表高度")


class SDUIList(SDUIComponent):
    """列表组件（用于文件列表、搜索结果等）"""
    type: Literal["SDUI.List"] = Field(default="SDUI.List")
    title: Optional[str] = Field(default=None, description="列表标题")
    items: List[Dict[str, Any]] = Field(description="列表项数据")
    item_template: Optional[Dict[str, Any]] = Field(
        default=None,
        description="列表项模板（Adaptive Cards 格式）"
    )
    show_index: Optional[bool] = Field(default=False, description="是否显示序号")
    max_items: Optional[int] = Field(default=None, description="最大显示项数")


class SDUIButton(SDUIComponent):
    """按钮组件（用于操作按钮）"""
    type: Literal["SDUI.Button"] = Field(default="SDUI.Button")
    title: str = Field(description="按钮文本")
    action_type: ActionType = Field(default=ActionType.SUBMIT, description="操作类型")
    action_id: Optional[str] = Field(default=None, description="操作ID")
    action_data: Optional[Dict[str, Any]] = Field(default=None, description="操作数据")
    style: Optional[str] = Field(default=None, description="按钮样式：'default', 'positive', 'destructive'")
    icon_url: Optional[str] = Field(default=None, description="图标URL")


# ============================================================================
# 导出
# ============================================================================

__all__ = [
    # 枚举
    "ActionType",
    "ElementType",
    "HorizontalAlignment",
    "VerticalAlignment",
    "FontSize",
    "FontWeight",
    "Color",
    "Spacing",
    # 基础元素
    "BaseElement",
    "TextBlock",
    "Image",
    "Action",
    "SubmitAction",
    "OpenUrlAction",
    "InputText",
    "InputNumber",
    "InputToggle",
    "InputChoice",
    "InputChoiceSet",
    "Container",
    "Column",
    "ColumnSet",
    # 主模型
    "AdaptiveCard",
    # Jachin 扩展组件
    "SDUIComponent",
    "SDUIProgressBar",
    "SDUIChart",
    "SDUIList",
    "SDUIButton",
    # 辅助函数
    "create_simple_card",
    "create_form_card",
    "create_list_card",
]
