"""可视化工具：将查询结果以图表形式展示。"""

import plotly.graph_objects as go

VISUALIZATION_KEYWORDS = [
    # 英文关键词
    "plot",
    "chart",
    "graph",
    "visualize",
    "visualise",
    "visualization",
    "visualisation",
    "show me",
    "display",
    "draw",
    "bar",
    "line",
    "pie",
    "scatter",
    "histogram",
    # 中文关键词
    "画图",
    "画一张",
    "画个",
    "绘图",
    "图表",
    "图示",
    "可视化",
    "柱状图",
    "条形图",
    "折线图",
    "饼图",
    "散点图",
    "直方图",
]


def wants_visualization(message: str) -> bool:
    """检测用户是否希望看到图表。"""
    lower = message.lower()
    return any(kw in lower for kw in VISUALIZATION_KEYWORDS)


def _get_numeric_columns(
    columns: list[str], rows: list[tuple]
) -> list[tuple[str, int]]:
    """返回数值列的 (列名, 索引) 列表。"""
    numeric = []
    for idx, col in enumerate(columns):
        if idx >= len(rows[0]):
            continue
        values = [row[idx] for row in rows if row[idx] is not None]
        if values and all(isinstance(v, (int, float)) for v in values):
            numeric.append((col, idx))
    return numeric


def create_chart(columns: list[str], rows: list[tuple]) -> go.Figure:
    """根据数据形态创建合适的 Plotly 图表。"""
    if not columns or not rows:
        raise ValueError("没有可可视化的数据")

    numeric_cols = _get_numeric_columns(columns, rows)

    if numeric_cols and len(columns) >= 2:
        x_col = columns[0]
        y_col, y_idx = numeric_cols[0]

        fig = go.Figure(
            data=[
                go.Bar(
                    x=[r[0] for r in rows],
                    y=[r[y_idx] for r in rows],
                    name=y_col,
                )
            ]
        )
        fig.update_layout(
            title_text=f"{y_col} by {x_col}",
            xaxis_title=x_col,
            yaxis_title=y_col,
        )
    elif numeric_cols:
        fig = go.Figure(
            data=[
                go.Scatter(
                    y=[r[numeric_cols[0][1]] for r in rows], mode="lines+markers"
                )
            ]
        )
        fig.update_layout(title_text=f"{numeric_cols[0][0]} values")
    else:
        fig = go.Figure(
            data=[
                go.Table(
                    header=dict(
                        values=columns, fill_color="paleturquoise", align="left"
                    ),
                    cells=dict(
                        values=list(zip(*rows)),
                        fill_color="lavender",
                        align="left",
                    ),
                )
            ]
        )

    return fig
