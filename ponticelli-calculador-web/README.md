# Calculador de Materiales – Ponticelli Hnos

Web app **funcionando** (sin npm / sin internet) con:
- **Header con logo obligatorio** (si falta, la app falla en `/api/health`).
- 3 pasos: Proyecto → Con qué + medidas → Resultado + **PDF**.
- PDF con **portada + logo** y encabezado/pie con marca.
- Config editable en **JSON** (sin tocar código).
- Modo **Admin** (local) con reporte de **cobertura** y editor del JSON.

## Requisitos
- Python 3.10+
- `reportlab` (ya instalado en este entorno)

## Ejecutar

```bash
cd ponticelli-calculador-web

# (opcional) habilitar admin
export ADMIN_PASSWORD='tu_clave'

python app.py
```

Abrir: `http://localhost:8000`

## Config editable (sin tocar código)
- Se guarda en: `data/config.json`
- Si no existe, se copia desde: `/mnt/data/ponticelli_materials_config.json`

## Admin
Entrar en `Admin` y cargar la clave.
- Cobertura: `FALTANTES` debe ser **0**.
- Podés editar parámetros (ej: reglas de consumibles) directamente en el JSON.

## Notas de cálculo
- La app calcula consumibles con reglas editables en `app.consumablesRules`.
- Para perfiles vendidos por **BAR**, aplica optimización simple de cortes **First-Fit Decreasing**.
- Siempre muestra “Ver cómo se calculó” (desglose) y agrega “Supuestos y redondeos” en el PDF.

