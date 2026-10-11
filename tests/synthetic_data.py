"""Genera un libro Excel ficticio en memoria para las pruebas de importación."""
from io import BytesIO

from openpyxl import Workbook

SYNTHETIC_COMPANIES = 70


def build_workbook():
    workbook = Workbook(write_only=True)
    sheet = workbook.create_sheet("Empresas")
    sheet.append(["Razón social", "NIT", "Tipo de empresa", "CIIU", "Sector", "Departamento", "Ciudad",
                  "Activos", "Pasivos", "Patrimonio", "Utilidad operacional"])
    first = None
    for index in range(1, 41):
        in_valle = index <= 32
        row = [f"Empresa Sintetica {index:03d} SAS", f"999000{index:03d}", "Sociedad", "5610",
               "Servicios", "VALLE DEL CAUCA" if in_valle else "BOGOTA D.C.",
               "CALI-VALLE" if in_valle else "BOGOTA D.C.", index * 1_000_000,
               index * 400_000, index * 600_000, index * 100_000]
        sheet.append(row)
        if index == 1:
            first = row

    sheet.append(first)
    for index in range(1, 31):
        sheet.append(["SAS", f"998000{index:03d}", "Sociedad", "5610", "Servicios",
                      "VALLE DEL CAUCA", "CALI-VALLE", None, None, None, None])
    sheet.append([None, "997000001", None, None, None, None, None, None, None, None, None])

    output = BytesIO()
    workbook.save(output)
    return output.getvalue()