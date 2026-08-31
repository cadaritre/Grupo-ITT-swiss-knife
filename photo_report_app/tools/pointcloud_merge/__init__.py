from ...tool_registry import ToolSpec


TOOL_SPEC = ToolSpec(
    tool_id="pointcloud_merge",
    title="Registro de nubes 3D",
    description="Ajusta una nube de dron a la base local del escáner mediante puntos XYZ conocidos y las fusiona en LAZ.",
    order=60,
    icon_text="3D",
    icon_color="#6B4BC3",
    icon_asset="pointcloud-merge.png",
    factory_path="photo_report_app.pointcloud_merge_tool:PointCloudMergeTool",
    version="1.1.0",
    data_category="pointclouds",
    data_folder="Nubes de puntos",
)
