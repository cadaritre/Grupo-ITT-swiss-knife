from ...tool_registry import ToolSpec


TOOL_SPEC = ToolSpec(
    tool_id="geospatial_converter",
    title="DXF ↔ KML/KMZ",
    description="Convierte geometría entre CAD y Google Earth, elige la zona UTM y revisa el resultado sobre un mapa.",
    order=40,
    icon_text="GEO",
    icon_color="#7653A6",
    icon_asset="geoconverter.png",
    factory_path="photo_report_app.geospatial_converter_tool:GeospatialConverterTool",
    version="1.0.0",
    data_category="conversions",
    data_folder="Conversiones",
)
