from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any


@dataclass
class QuoteItem:
    title: str = "Nuevo concepto"
    description: str = ""
    subitems: list[str] = field(default_factory=list)
    unit: str = "Lote"
    quantity: float = 1.0
    unit_price: float = 0.0

    @property
    def amount(self) -> float:
        return round(self.quantity * self.unit_price, 2)

@dataclass
class QuoteImage:
    path: str
    caption: str = ""


@dataclass
class QuoteData:
    quote_number: str = ""
    quote_date: str = field(default_factory=lambda: date.today().strftime("%d/%m/%Y"))
    client_name: str = ""
    contact: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    project_title: str = "Servicios de ingeniería"
    notes: str = "Se requiere anticipo para programar los trabajos."
    currency: str = "MXN"
    include_vat: bool = True
    vat_rate: float = 16.0
    advance_percent: float = 50.0
    validity_days: int = 15
    delivery_time: str = "Por confirmar de acuerdo con el alcance."
    payment_terms: str = "Transferencia electrónica / Cheque / Efectivo"
    bank: str = "HSBC"
    clabe: str = "021150040136974533"
    account: str = "4013697453"
    prepared_by: str = "ING. CARLOS RIVERA ABAID"
    language: str = "es"
    spelling_checked: bool = False
    items: list[QuoteItem] = field(default_factory=list)
    images: list[QuoteImage] = field(default_factory=list)
    version: int = 1

    @property
    def subtotal(self) -> float:
        return round(sum(item.amount for item in self.items), 2)

    @property
    def vat(self) -> float:
        return round(self.subtotal * self.vat_rate / 100, 2) if self.include_vat else 0.0

    @property
    def total(self) -> float:
        return round(self.subtotal + self.vat, 2)

    @property
    def advance(self) -> float:
        return round(self.total * self.advance_percent / 100, 2)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "QuoteData":
        allowed = set(cls.__dataclass_fields__)
        values = {key: value for key, value in raw.items() if key in allowed and key not in {"items", "images"}}
        item_fields = set(QuoteItem.__dataclass_fields__)
        values["items"] = [QuoteItem(**{key: value for key, value in item.items() if key in item_fields}) for item in raw.get("items", [])]
        values["images"] = [QuoteImage(**image) for image in raw.get("images", [])]
        return cls(**values)

    def save(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return target

    @classmethod
    def load(cls, path: str | Path) -> "QuoteData":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


CONCEPT_TEMPLATES: dict[str, QuoteItem] = {
    "Levantamiento topográfico": QuoteItem(
        title="Levantamiento topográfico planimétrico y altimétrico",
        description="Levantamiento del área indicada por el cliente, georreferenciado y elaborado con equipo de precisión.",
        subitems=[
            "Planimetría y altimetría del terreno.",
            "Curvas de nivel y representación de infraestructura visible.",
            "Plano digital en formatos DWG y PDF.",
            "Banco de nivel en un elemento fijo dentro del área de trabajo.",
            "Coordenadas UTM ligadas al marco geodésico oficial aplicable.",
        ],
        unit="Lote",
    ),
    "Vuelo con dron RTK": QuoteItem(
        title="Levantamiento fotogramétrico con dron RTK",
        description="Vuelo fotogramétrico del polígono indicado, sujeto a condiciones climáticas, permisos y restricciones de vuelo.",
        subitems=[
            "Ortomosaico georreferenciado.",
            "Modelo digital de superficie y curvas de nivel.",
            "Control terrestre con GNSS de precisión cuando sea requerido.",
            "Entrega digital de productos en formatos compatibles con AutoCAD.",
        ],
        unit="Lote",
    ),
    "Escaneo láser 3D": QuoteItem(
        title="Levantamiento con escáner láser 3D",
        description="Captura tridimensional de las áreas visibles y accesibles mediante escáner láser terrestre.",
        subitems=[
            "Escaneo a resolución estándar.",
            "Registro y procesamiento de nube de puntos.",
            "Entrega de nube de puntos en formato RCP/E57.",
            "No incluye elementos ocultos, obstruidos o inaccesibles.",
        ],
        unit="Lote",
    ),
    "Modelado BIM LOD 300": QuoteItem(
        title="Modelado arquitectónico BIM LOD 300",
        description="Modelado en Autodesk Revit a partir de nube de puntos proporcionada o capturada durante el levantamiento.",
        subitems=[
            "Modelado de interiores, exteriores y azotea visibles.",
            "Muros, puertas, ventanas, escaleras, losas y elementos medibles.",
            "Precisión sujeta a visibilidad, accesibilidad y calidad de la nube de puntos.",
            "Entrega de modelo RVT y láminas PDF acordadas.",
        ],
        unit="Lote",
    ),
    "Control geodésico": QuoteItem(
        title="Establecimiento de control geodésico",
        description="Materialización y observación de puntos de control para liga geodésica del proyecto.",
        subitems=[
            "Observaciones GNSS estáticas.",
            "Postproceso con estaciones de la RGNA del INEGI.",
            "Coordenadas UTM y reporte de procesamiento.",
            "Altura elipsoidal, salvo que se acuerde una referencia vertical distinta.",
        ],
        unit="Punto",
    ),
    "Trazo y replanteo": QuoteItem(
        title="Trazo y replanteo topográfico",
        description="Replanteo en campo de los puntos, ejes o niveles proporcionados por el cliente.",
        subitems=[
            "Brigada de topografía con equipo GNSS RTK o estación total.",
            "Marcado físico de puntos accesibles.",
            "Reporte de coordenadas de los puntos trazados.",
            "El cliente deberá entregar información de proyecto aprobada antes de iniciar.",
        ],
        unit="Día",
    ),
    "Brigada con escáner": QuoteItem(
        title="Brigada de ingeniería con escáner 3D",
        description="Servicio diario de operador y escáner FARO Focus para captura en campo.",
        subitems=[
            "Incluye hasta 5 horas de escaneo diario.",
            "No incluye registro ni procesamiento de escaneos.",
            "El cliente deberá proporcionar los accesos y condiciones de seguridad.",
        ],
        unit="Día",
    ),
    "Deslinde y polígono catastral": QuoteItem(
        title="Deslinde topográfico y elaboración de polígono catastral",
        description="Levantamiento de límites físicos y análisis de la información documental proporcionada por el cliente.",
        subitems=[
            "Levantamiento de bardas, cercos, mojoneras y elementos físicos visibles.",
            "Revisión y acomodo de escrituras o antecedentes proporcionados por el cliente.",
            "Cuadro de construcción con rumbos, distancias, vértices y coordenadas.",
            "Plano en formatos DWG y PDF.",
            "No incluye resolución de controversias legales ni certificación pericial, salvo contratación expresa.",
        ],
        unit="Lote",
    ),
    "Estudio topográfico hidrológico": QuoteItem(
        title="Levantamiento topográfico para estudio hidrológico",
        description="Topografía detallada del cauce y su zona de influencia para apoyar el análisis hidráulico o hidrológico.",
        subitems=[
            "Levantamiento del eje del cauce aguas arriba y aguas abajo.",
            "Secciones transversales en puntos representativos y cambios de geometría.",
            "Perfil longitudinal y niveles de estructuras, cruces y obras existentes visibles.",
            "Curvas de nivel y modelo digital del terreno.",
            "Memoria fotográfica georreferenciada y planos DWG/PDF.",
        ],
        unit="Sitio",
    ),
    "Volumetría y cubicaciones": QuoteItem(
        title="Levantamiento para cálculo de volúmenes",
        description="Medición topográfica de superficies para determinar volúmenes de corte, relleno, acopio o avance de obra.",
        subitems=[
            "Levantamiento con GNSS RTK, estación total, dron RTK o escáner según las condiciones del sitio.",
            "Generación de superficies y comparación contra terreno natural, proyecto o levantamiento anterior.",
            "Cálculo de volúmenes de corte y relleno.",
            "Plano de resultados y reporte de cubicaciones.",
            "Entrega digital en PDF y archivos de superficie acordados.",
        ],
        unit="Lote",
    ),
    "Monitoreo de deformaciones": QuoteItem(
        title="Monitoreo topográfico de asentamientos y deformaciones",
        description="Campaña de medición de puntos de control para identificar desplazamientos respecto a una lectura base.",
        subitems=[
            "Revisión o instalación de testigos y puntos de monitoreo accesibles.",
            "Lectura con nivel de precisión, estación total o GNSS según el objetivo.",
            "Comparación contra lectura inicial y campañas anteriores.",
            "Tablas de desplazamientos y representación gráfica de tendencias.",
            "Reporte técnico por campaña; la interpretación estructural deberá realizarla el especialista correspondiente.",
        ],
        unit="Campaña",
    ),
    "Levantamiento de infraestructura urbana": QuoteItem(
        title="Levantamiento topográfico de infraestructura urbana",
        description="Inventario y representación de infraestructura visible en vialidades y áreas exteriores.",
        subitems=[
            "Calles, banquetas, guarniciones, camellones y accesos.",
            "Postes, registros, pozos de visita, válvulas y mobiliario urbano visible.",
            "Niveles superiores de brocales, tapas y elementos accesibles.",
            "Planimetría, altimetría y curvas de nivel según el alcance.",
            "Entrega de plano georreferenciado en DWG y PDF.",
            "No incluye detección de instalaciones enterradas ni apertura de registros restringidos.",
        ],
        unit="Lote",
    ),
    "Planos de estado actual As-Built": QuoteItem(
        title="Levantamiento y elaboración de planos de estado actual (As-Built)",
        description="Documentación geométrica de los elementos construidos, visibles y accesibles al momento del levantamiento.",
        subitems=[
            "Plantas arquitectónicas con muros, puertas, ventanas y niveles principales.",
            "Fachadas y cortes en los ejes acordados.",
            "Ubicación de columnas, estructuras y equipos visibles incluidos en el alcance.",
            "Levantamiento con escáner 3D o equipo topográfico según necesidades.",
            "Entrega en formatos DWG y PDF.",
            "No incluye calas, desmontajes ni elementos ocultos por plafones o recubrimientos.",
        ],
        unit="Lote",
    ),
    "Georreferenciación de predio": QuoteItem(
        title="Georreferenciación de predio y liga a la RGNA del INEGI",
        description="Determinación de coordenadas del polígono mediante observaciones GNSS y procesamiento geodésico.",
        subitems=[
            "Observación de puntos de control con receptores GNSS de precisión.",
            "Postproceso ligado a estaciones de la Red Geodésica Nacional Activa.",
            "Coordenadas UTM y geográficas en el marco de referencia aplicable.",
            "Cuadro de construcción y croquis de ubicación de vértices.",
            "Reporte de procesamiento y archivos de coordenadas.",
        ],
        unit="Lote",
    ),
}


CONCEPT_TEMPLATES_EN: dict[str, QuoteItem] = {
    "Levantamiento topográfico": QuoteItem(
        "Planimetric and altimetric topographic survey",
        "Georeferenced survey of the area specified by the client, performed with precision equipment.",
        ["Site planimetry and altimetry.", "Contour lines and representation of visible infrastructure.", "Digital drawing in DWG and PDF formats.", "Benchmark on a fixed element within the work area.", "UTM coordinates tied to the applicable official geodetic reference frame."], "Lot",
    ),
    "Vuelo con dron RTK": QuoteItem(
        "Photogrammetric survey with RTK drone",
        "Photogrammetric flight over the specified polygon, subject to weather conditions, permits and flight restrictions.",
        ["Georeferenced orthomosaic.", "Digital surface model and contour lines.", "Ground control with precision GNSS when required.", "Digital delivery in AutoCAD-compatible formats."], "Lot",
    ),
    "Escaneo láser 3D": QuoteItem(
        "3D laser scanner survey", "Three-dimensional capture of visible and accessible areas using a terrestrial laser scanner.",
        ["Standard-resolution scanning.", "Point-cloud registration and processing.", "Point-cloud delivery in RCP/E57 format.", "Hidden, obstructed or inaccessible elements are not included."], "Lot",
    ),
    "Modelado BIM LOD 300": QuoteItem(
        "LOD 300 architectural BIM modeling", "Autodesk Revit modeling based on a point cloud supplied or captured during the survey.",
        ["Modeling of visible interiors, exteriors and roof areas.", "Walls, doors, windows, stairs, slabs and measurable elements.", "Accuracy subject to visibility, accessibility and point-cloud quality.", "Delivery of the RVT model and agreed PDF sheets."], "Lot",
    ),
    "Control geodésico": QuoteItem(
        "Establishment of geodetic control", "Monumentation and observation of control points for the project's geodetic reference.",
        ["Static GNSS observations.", "Post-processing with stations from INEGI's Active National Geodetic Network.", "UTM coordinates and processing report.", "Ellipsoidal height unless a different vertical reference is agreed."], "Point",
    ),
    "Trazo y replanteo": QuoteItem(
        "Topographic construction staking", "Field staking of points, axes or elevations supplied by the client.",
        ["Survey crew with GNSS RTK equipment or total station.", "Physical marking of accessible points.", "Coordinate report for staked points.", "The client must provide approved project information before work begins."], "Day",
    ),
    "Brigada con escáner": QuoteItem(
        "Engineering crew with 3D scanner", "Daily service including an operator and FARO Focus scanner for field capture.",
        ["Includes up to 5 hours of scanning per day.", "Scan registration and processing are not included.", "The client must provide access and safe working conditions."], "Day",
    ),
    "Deslinde y polígono catastral": QuoteItem(
        "Boundary survey and cadastral polygon", "Survey of physical boundaries and analysis of documentary information supplied by the client.",
        ["Survey of walls, fences, monuments and visible physical elements.", "Review and placement of deeds or background documents supplied by the client.", "Survey table with bearings, distances, vertices and coordinates.", "Drawing in DWG and PDF formats.", "Legal dispute resolution and expert certification are excluded unless expressly contracted."], "Lot",
    ),
    "Estudio topográfico hidrológico": QuoteItem(
        "Topographic survey for hydrologic study", "Detailed topography of the channel and its area of influence to support hydraulic or hydrologic analysis.",
        ["Survey of the channel centerline upstream and downstream.", "Cross sections at representative points and geometry changes.", "Longitudinal profile and elevations of visible structures, crossings and existing works.", "Contour lines and digital terrain model.", "Georeferenced photographic record and DWG/PDF drawings."], "Site",
    ),
    "Volumetría y cubicaciones": QuoteItem(
        "Survey for volume calculations", "Topographic measurement of surfaces to determine cut, fill, stockpile or construction-progress volumes.",
        ["Survey with GNSS RTK, total station, RTK drone or scanner according to site conditions.", "Surface generation and comparison against existing ground, design or a previous survey.", "Cut-and-fill volume calculations.", "Results drawing and volume report.", "Digital delivery in PDF and agreed surface-file formats."], "Lot",
    ),
    "Monitoreo de deformaciones": QuoteItem(
        "Settlement and deformation monitoring", "Measurement campaign of control points to identify displacement relative to a baseline reading.",
        ["Review or installation of accessible monitoring marks and points.", "Readings with a precision level, total station or GNSS according to the objective.", "Comparison against the baseline and previous campaigns.", "Displacement tables and graphical trend representation.", "Technical report per campaign; structural interpretation must be performed by the corresponding specialist."], "Campaign",
    ),
    "Levantamiento de infraestructura urbana": QuoteItem(
        "Urban infrastructure topographic survey", "Inventory and representation of visible infrastructure along roads and outdoor areas.",
        ["Streets, sidewalks, curbs, medians and access points.", "Poles, utility boxes, manholes, valves and visible street furniture.", "Top elevations of rims, covers and accessible elements.", "Planimetry, altimetry and contour lines according to scope.", "Delivery of a georeferenced drawing in DWG and PDF.", "Detection of buried utilities and opening of restricted utility boxes are excluded."], "Lot",
    ),
    "Planos de estado actual As-Built": QuoteItem(
        "Existing-condition survey and As-Built drawings", "Geometric documentation of constructed elements that are visible and accessible at the time of survey.",
        ["Architectural plans with walls, doors, windows and main elevations.", "Elevations and sections along the agreed axes.", "Location of columns, structures and visible equipment included in the scope.", "Survey with a 3D scanner or topographic equipment as required.", "Delivery in DWG and PDF formats.", "Exploratory openings, dismantling and elements concealed by ceilings or finishes are excluded."], "Lot",
    ),
    "Georreferenciación de predio": QuoteItem(
        "Property georeferencing tied to INEGI's national network", "Determination of polygon coordinates through GNSS observations and geodetic processing.",
        ["Observation of control points with precision GNSS receivers.", "Post-processing tied to stations of the Active National Geodetic Network.", "UTM and geographic coordinates in the applicable reference frame.", "Survey table and location sketch of vertices.", "Processing report and coordinate files."], "Lot",
    ),
}


def translate_template_item(item: QuoteItem, target_language: str) -> None:
    target_english = target_language == "en"
    source_templates = CONCEPT_TEMPLATES if target_english else CONCEPT_TEMPLATES_EN
    target_templates = CONCEPT_TEMPLATES_EN if target_english else CONCEPT_TEMPLATES
    for name, source in source_templates.items():
        target = target_templates[name]
        if item.title == source.title:
            item.title = target.title
        if item.description == source.description:
            item.description = target.description
        translations = dict(zip(source.subitems, target.subitems))
        item.subitems = [translations.get(value, value) for value in item.subitems]
        if item.unit == source.unit:
            item.unit = target.unit


def clone_template(name: str, language: str = "es") -> QuoteItem:
    source = CONCEPT_TEMPLATES_EN[name] if language == "en" else CONCEPT_TEMPLATES[name]
    return QuoteItem(**asdict(source))
