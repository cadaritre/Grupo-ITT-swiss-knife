from ...tool_registry import ToolSpec


TOOL_SPEC = ToolSpec(
    tool_id="sketches",
    title="Croquis de ubicación",
    description="Selecciona un área sobre el mapa, vectoriza cartografía y exporta PDF o DXF UTM.",
    order=30,
    icon_text="MAP",
    icon_color="#138A72",
    icon_asset="sketch.png",
    factory_path="photo_report_app.sketch_tool:LocationSketchTool",
    version="1.0.0",
    data_category="sketches",
    data_folder="Croquis",
)
