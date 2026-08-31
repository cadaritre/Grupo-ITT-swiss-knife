from ...tool_registry import ToolSpec


TOOL_SPEC = ToolSpec(
    tool_id="reports",
    title="Reportes fotográficos",
    description="Ordena fotografías, agrega descripciones y genera un PDF con croquis opcional.",
    order=10,
    icon_text="RF",
    icon_color="#0B7FAB",
    icon_asset="reports.png",
    factory_path="photo_report_app.report_tool:ReportTool",
    version="1.2.0",
    data_category="reports",
    data_folder="Reportes Fotograficos",
)
