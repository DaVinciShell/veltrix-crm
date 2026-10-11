"""Restaura una copia de seguridad. Cierre el CRM antes.  Uso:  python restaurar.py data\\respaldos\\crm_auto_YYYYMMDD_HHMMSS.db"""
import os
import sys

import mejoras_seguridad as MS

if len(sys.argv) != 2:
    sys.exit(__doc__)
base = os.path.dirname(os.path.abspath(__file__))
db = os.path.join(os.environ.get("VELTRIX_DATA", os.path.join(base, "data")), "crm.db")
try:
    r = MS.restaurar(db, os.path.abspath(sys.argv[1]))
except Exception as e:  # noqa: BLE001
    sys.exit(f"NO se restauró: {e}")
print(f"Restaurado: {r['empresas']} empresas. Base anterior guardada en: {r['copia_de_seguridad_previa']}")
