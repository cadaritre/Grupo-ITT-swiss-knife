from ...tool_registry import ToolSpec


TOOL_SPEC = ToolSpec(
    tool_id="ppk_drone",
    title="PPK Dron",
    description="Procesa RINEX y disparos MRK con RTKLIB, corrige fotografías DJI y documenta la calidad FIX o provisional.",
    order=70,
    icon_text="PPK",
    icon_color="#087F8C",
    icon_asset="ppk-drone.png",
    factory_path="photo_report_app.ppk_tool:PPKTool",
    version="1.0.0",
    data_category="ppk",
    data_folder="PPK Dron",
)
