from __future__ import annotations


LABELS = {
    "es": {
        "document": "Cotización", "no_number": "SIN FOLIO", "page": "Página",
        "date": "FECHA", "name_company": "NOMBRE / EMPRESA", "contact": "CONTACTO",
        "email": "CORREO", "phone": "TELÉFONO", "location": "LOCALIZACIÓN",
        "concept": "CONCEPTO", "unit": "UNIDAD", "quantity": "CANT.",
        "unit_price": "P.U.", "amount": "IMPORTE", "continued": "continuación",
        "no_description": "Sin descripción.", "subtotal": "SUBTOTAL", "vat": "IVA",
        "total": "TOTAL", "advance": "ANTICIPO", "notes": "NOTAS", "currency": "MONEDA",
        "payment_terms": "FORMA DE PAGO", "validity": "VIGENCIA", "days": "días",
        "bank_clabe": "BANCO / CLABE", "account": "CUENTA", "delivery_time": "TIEMPO DE ENTREGA",
        "quoted_services": "SERVICIOS COTIZADOS", "no_items": "No se han agregado conceptos.",
        "place_date": "Chihuahua, Chih., a {date}", "sincerely": "ATENTAMENTE",
        "reference_image": "Imagen de referencia {number}", "image": "IMAGEN",
    },
    "en": {
        "document": "Quotation", "no_number": "NO NUMBER", "page": "Page",
        "date": "DATE", "name_company": "NAME / COMPANY", "contact": "CONTACT",
        "email": "EMAIL", "phone": "PHONE", "location": "LOCATION",
        "concept": "DESCRIPTION", "unit": "UNIT", "quantity": "QTY.",
        "unit_price": "UNIT PRICE", "amount": "AMOUNT", "continued": "continued",
        "no_description": "No description.", "subtotal": "SUBTOTAL", "vat": "VAT",
        "total": "TOTAL", "advance": "ADVANCE", "notes": "NOTES", "currency": "CURRENCY",
        "payment_terms": "PAYMENT TERMS", "validity": "VALIDITY", "days": "days",
        "bank_clabe": "BANK / CLABE", "account": "ACCOUNT", "delivery_time": "DELIVERY TIME",
        "quoted_services": "QUOTED SERVICES", "no_items": "No items have been added.",
        "place_date": "Chihuahua, Chih., {date}", "sincerely": "SINCERELY",
        "reference_image": "Reference image {number}", "image": "IMAGE",
    },
}


STANDARD_VALUES = {
    "project_title": ("Servicios de ingeniería", "Engineering services"),
    "notes": ("Se requiere anticipo para programar los trabajos.", "An advance payment is required to schedule the work."),
    "delivery_time": ("Por confirmar de acuerdo con el alcance.", "To be confirmed according to the agreed scope."),
    "payment_terms": ("Transferencia electrónica / Cheque / Efectivo", "Bank transfer / Check / Cash"),
}

STANDARD_UNITS = {
    "Lote": "Lot", "Punto": "Point", "Día": "Day", "Sitio": "Site", "Campaña": "Campaign",
}

STANDARD_SIGNERS = {
    "ING. EDGAR TREVIZO": "ENG. EDGAR TREVIZO",
    "ING. CARLOS RIVERA ABAID": "ENG. CARLOS RIVERA ABAID",
}


def labels(language: str) -> dict[str, str]:
    return LABELS["en" if language == "en" else "es"]


def translate_standard_quote_values(quote, target_language: str) -> None:
    """Translate only built-in boilerplate; custom user text is never overwritten."""
    from .quotation_models import translate_template_item

    target = "en" if target_language == "en" else "es"
    source_index, target_index = (0, 1) if target == "en" else (1, 0)
    for attribute, pair in STANDARD_VALUES.items():
        current = str(getattr(quote, attribute, "") or "").strip()
        if current == pair[source_index]:
            setattr(quote, attribute, pair[target_index])
    unit_map = STANDARD_UNITS if target == "en" else {value: key for key, value in STANDARD_UNITS.items()}
    signer_map = STANDARD_SIGNERS if target == "en" else {value: key for key, value in STANDARD_SIGNERS.items()}
    if quote.prepared_by in signer_map:
        quote.prepared_by = signer_map[quote.prepared_by]
    for item in quote.items:
        if item.unit in unit_map:
            item.unit = unit_map[item.unit]
        translate_template_item(item, target)
