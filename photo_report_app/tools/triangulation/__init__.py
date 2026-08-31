from ...tool_registry import ToolSpec


TOOL_SPEC = ToolSpec(
    tool_id="triangulation",
    title="Triangulación DXF",
    description="Genera superficies TIN, zonificaciones por pendiente y escurrimientos con vista previa y rangos editables.",
    order=50,
    icon_text="TIN",
    icon_color="#B8662C",
    icon_asset="__triangulation__",
    factory_path="photo_report_app.triangulation_tool:TriangulationTool",
    version="1.1.0",
    data_category=None,
)
