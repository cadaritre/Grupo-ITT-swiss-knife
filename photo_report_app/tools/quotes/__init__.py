from ...tool_registry import ToolSpec


TOOL_SPEC = ToolSpec(
    tool_id="quotes",
    title="Cotizaciones",
    description="Construye conceptos, calcula IVA, revisa ortografía, agrega imágenes y conserva una versión editable.",
    order=20,
    icon_text="COT",
    icon_color="#173B5F",
    icon_asset="quotes.png",
    factory_path="photo_report_app.quotation_tool:QuotationTool",
    version="1.1.0",
    data_category="quotes",
    data_folder="Cotizaciones",
)
