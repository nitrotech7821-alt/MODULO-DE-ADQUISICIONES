
import os
import re
import sqlite3
from datetime import datetime, date
from io import BytesIO
from pathlib import Path

import pandas as pd
import streamlit as st

# ============================================================
# CONFIGURACIÓN GENERAL
# ============================================================
st.set_page_config(
    page_title="Sistema de Adquisiciones DIF",
    page_icon="🛒",
    layout="wide"
)

BASE_DIR = Path("sistema_adquisiciones")
DOCS_DIR = BASE_DIR / "documentos"
DB_PATH = BASE_DIR / "adquisiciones.db"

BASE_DIR.mkdir(exist_ok=True)
DOCS_DIR.mkdir(exist_ok=True)

ESTATUS = [
    "Capturada",
    "En firma",
    "Firmada",
    "En cotización",
    "Compra realizada",
    "Producto recibido",
    "Evidencia cargada",
    "Entregado",
    "Firmado recibido",
    "Cerrada",
    "Cancelada",
]

TIPOS_DOCUMENTO = [
    "Requisición",
    "Requisición firmada",
    "Cotización",
    "Factura PDF",
    "XML",
    "Evidencia de compra",
    "Firma de recibido",
    "Otro",
]

USUARIOS = {
    "admin": {"password": "1234", "rol": "Administrador"},
    "adquisiciones": {"password": "1234", "rol": "Adquisiciones"},
    "consulta": {"password": "1234", "rol": "Consulta"},
}

# ============================================================
# ESTILO
# ============================================================
st.markdown("""
<style>
.stApp {
    background:
        radial-gradient(circle at top left, rgba(8,123,117,0.16), transparent 32%),
        radial-gradient(circle at bottom right, rgba(233,78,27,0.16), transparent 32%),
        linear-gradient(135deg, #EEF8F5 0%, #FFF7E7 54%, #FDE0CF 100%);
}
.block-container { padding-top: 24px; }
.header-card {
    background: linear-gradient(135deg, rgba(219,246,241,0.98), rgba(255,242,216,0.98));
    padding: 26px;
    border-radius: 24px;
    box-shadow: 0px 8px 24px rgba(0,0,0,0.11);
    text-align: center;
    margin-bottom: 22px;
}
.header-card h1 {
    color: #087B75;
    font-weight: 900;
    margin-bottom: 5px;
}
.card {
    background: rgba(255,255,255,0.92);
    padding: 22px;
    border-radius: 18px;
    box-shadow: 0px 5px 15px rgba(0,0,0,0.08);
    border-left: 7px solid #087B75;
    margin-bottom: 18px;
}
.stButton > button {
    background: linear-gradient(90deg, #E94E1B, #F2B233);
    color: white;
    border: none;
    border-radius: 14px;
    padding: 10px;
    font-weight: 900;
    width: 100%;
}
.stDownloadButton > button {
    background: linear-gradient(90deg, #087B75, #14A39A);
    color: white;
    border: none;
    border-radius: 14px;
    padding: 10px;
    font-weight: 900;
    width: 100%;
}
</style>
""", unsafe_allow_html=True)

# ============================================================
# BASE DE DATOS
# ============================================================
def conectar():
    return sqlite3.connect(DB_PATH, check_same_thread=False)

def inicializar_db():
    con = conectar()
    cur = con.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS requisiciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            folio TEXT UNIQUE,
            fecha TEXT,
            concepto TEXT,
            area TEXT,
            solicitante TEXT,
            proveedor TEXT,
            factura TEXT,
            fecha_factura TEXT,
            importe REAL,
            cargado_a TEXT,
            estatus TEXT,
            observaciones TEXT,
            fecha_captura TEXT,
            usuario TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS areas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT UNIQUE,
            activa INTEGER DEFAULT 1
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS proveedores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT UNIQUE,
            activo INTEGER DEFAULT 1
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS documentos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            requisicion_id INTEGER,
            folio TEXT,
            tipo_documento TEXT,
            nombre_archivo TEXT,
            ruta_archivo TEXT,
            fecha_subida TEXT,
            usuario TEXT
        )
    """)

    con.commit()
    con.close()

inicializar_db()

# ============================================================
# FUNCIONES
# ============================================================
def login():
    if "logueado_adq" not in st.session_state:
        st.session_state.logueado_adq = False
        st.session_state.usuario_adq = ""
        st.session_state.rol_adq = ""

    if st.session_state.logueado_adq:
        return True

    st.markdown("""
    <div class="header-card">
        <h1>🛒 Sistema de Adquisiciones</h1>
        <p>Control de requisiciones, cotizaciones, compras y evidencias</p>
    </div>
    """, unsafe_allow_html=True)

    st.markdown('<div class="card">', unsafe_allow_html=True)
    usuario = st.text_input("Usuario")
    password = st.text_input("Contraseña", type="password")

    if st.button("🔐 Entrar"):
        if usuario in USUARIOS and password == USUARIOS[usuario]["password"]:
            st.session_state.logueado_adq = True
            st.session_state.usuario_adq = usuario
            st.session_state.rol_adq = USUARIOS[usuario]["rol"]
            st.rerun()
        else:
            st.error("Usuario o contraseña incorrectos.")

    st.info("Usuarios iniciales: admin / adquisiciones / consulta. Contraseña: 1234")
    st.markdown("</div>", unsafe_allow_html=True)
    return False

if not login():
    st.stop()

def limpiar_texto(valor):
    if pd.isna(valor):
        return ""
    return str(valor).strip()

def limpiar_folio(valor):
    texto = limpiar_texto(valor)
    texto = texto.replace(".0", "")
    return texto.strip()

def limpiar_importe(valor):
    if pd.isna(valor) or valor == "":
        return 0.0
    texto = str(valor).replace("$", "").replace(",", "").strip()
    try:
        return float(texto)
    except Exception:
        return 0.0

def fecha_a_texto(valor):
    if pd.isna(valor) or valor == "":
        return ""
    try:
        return pd.to_datetime(valor).strftime("%Y-%m-%d")
    except Exception:
        return str(valor)

def normalizar_nombre_archivo(texto):
    texto = str(texto).upper().strip()
    reemplazos = {
        "Á": "A", "É": "E", "Í": "I", "Ó": "O", "Ú": "U", "Ñ": "N",
        "á": "A", "é": "E", "í": "I", "ó": "O", "ú": "U", "ñ": "N"
    }
    for a, b in reemplazos.items():
        texto = texto.replace(a, b)
    texto = re.sub(r"[^A-Z0-9_\- ]", "", texto)
    texto = texto.replace(" ", "_")
    return texto[:80] if texto else "ARCHIVO"

def obtener_df_requisiciones():
    con = conectar()
    df = pd.read_sql_query("SELECT * FROM requisiciones ORDER BY id DESC", con)
    con.close()
    return df

def obtener_areas():
    con = conectar()
    df = pd.read_sql_query("SELECT nombre FROM areas WHERE activa = 1 ORDER BY nombre", con)
    con.close()
    return df["nombre"].tolist()

def obtener_proveedores():
    con = conectar()
    df = pd.read_sql_query("SELECT nombre FROM proveedores WHERE activo = 1 ORDER BY nombre", con)
    con.close()
    return df["nombre"].tolist()

def agregar_area(nombre):
    nombre = nombre.strip().upper()
    if not nombre:
        return
    con = conectar()
    cur = con.cursor()
    cur.execute("INSERT OR IGNORE INTO areas(nombre, activa) VALUES (?, 1)", (nombre,))
    con.commit()
    con.close()

def agregar_proveedor(nombre):
    nombre = nombre.strip().upper()
    if not nombre:
        return
    con = conectar()
    cur = con.cursor()
    cur.execute("INSERT OR IGNORE INTO proveedores(nombre, activo) VALUES (?, 1)", (nombre,))
    con.commit()
    con.close()

def generar_folio():
    con = conectar()
    cur = con.cursor()
    cur.execute("SELECT COUNT(*) FROM requisiciones")
    total = cur.fetchone()[0] + 1
    con.close()
    return f"REQ-{datetime.now().year}-{total:05d}"

def insertar_requisicion(datos):
    con = conectar()
    cur = con.cursor()

    cur.execute("""
        INSERT OR IGNORE INTO requisiciones (
            folio, fecha, concepto, area, solicitante, proveedor, factura,
            fecha_factura, importe, cargado_a, estatus, observaciones,
            fecha_captura, usuario
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        datos.get("folio", ""),
        datos.get("fecha", ""),
        datos.get("concepto", ""),
        datos.get("area", ""),
        datos.get("solicitante", ""),
        datos.get("proveedor", ""),
        datos.get("factura", ""),
        datos.get("fecha_factura", ""),
        datos.get("importe", 0.0),
        datos.get("cargado_a", ""),
        datos.get("estatus", "Capturada"),
        datos.get("observaciones", ""),
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        st.session_state.usuario_adq
    ))

    con.commit()
    con.close()

    if datos.get("area"):
        agregar_area(datos.get("area"))
    if datos.get("proveedor"):
        agregar_proveedor(datos.get("proveedor"))

def actualizar_estatus(req_id, nuevo_estatus, observaciones_extra=""):
    con = conectar()
    cur = con.cursor()
    cur.execute(
        "UPDATE requisiciones SET estatus = ?, observaciones = COALESCE(observaciones,'') || ? WHERE id = ?",
        (nuevo_estatus, f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M')}] {observaciones_extra}" if observaciones_extra else "", req_id)
    )
    con.commit()
    con.close()

def subir_documento(req_id, folio, tipo_documento, archivo):
    if archivo is None:
        return ""

    carpeta_folio = DOCS_DIR / normalizar_nombre_archivo(folio)
    carpeta_folio.mkdir(parents=True, exist_ok=True)

    extension = Path(archivo.name).suffix.lower()
    fecha = datetime.now().strftime("%Y%m%d_%H%M%S")
    nombre_archivo = f"{fecha}_{normalizar_nombre_archivo(tipo_documento)}{extension}"
    ruta = carpeta_folio / nombre_archivo

    with open(ruta, "wb") as f:
        f.write(archivo.getbuffer())

    con = conectar()
    cur = con.cursor()
    cur.execute("""
        INSERT INTO documentos (
            requisicion_id, folio, tipo_documento, nombre_archivo,
            ruta_archivo, fecha_subida, usuario
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        req_id,
        folio,
        tipo_documento,
        nombre_archivo,
        str(ruta),
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        st.session_state.usuario_adq
    ))
    con.commit()
    con.close()
    return str(ruta)

def obtener_documentos(req_id):
    con = conectar()
    df = pd.read_sql_query(
        "SELECT * FROM documentos WHERE requisicion_id = ? ORDER BY id DESC",
        con,
        params=(req_id,)
    )
    con.close()
    return df

def crear_excel(df):
    salida = BytesIO()
    df.to_excel(salida, index=False)
    salida.seek(0)
    return salida

def encontrar_columna(df, opciones):
    columnas = {str(c).strip().upper(): c for c in df.columns}
    for op in opciones:
        op = op.upper()
        for col_upper, col_original in columnas.items():
            if col_upper == op or op in col_upper:
                return col_original
    return None

def importar_excel(uploaded_file):
    df = pd.read_excel(uploaded_file)

    col_fecha = encontrar_columna(df, ["FECHA"])
    col_folio = encontrar_columna(df, ["# REQUI", "REQUI", "FOLIO"])
    col_concepto = encontrar_columna(df, ["CONCEPTO", "DESCRIPCION", "DESCRIPCIÓN"])
    col_area = encontrar_columna(df, ["AREA", "ÁREA"])
    col_proveedor = encontrar_columna(df, ["PROVEEDOR"])
    col_factura = encontrar_columna(df, ["FACT", "FACTURA"])
    col_importe = encontrar_columna(df, ["IMPORTE", "TOTAL", "MONTO"])
    col_cargado = encontrar_columna(df, ["CARGADO A", "CARGADO"])

    total_insertados = 0

    for _, row in df.iterrows():
        folio = limpiar_folio(row[col_folio]) if col_folio else ""
        concepto = limpiar_texto(row[col_concepto]) if col_concepto else ""

        if not folio and not concepto:
            continue

        if not folio:
            folio = generar_folio()

        datos = {
            "folio": folio,
            "fecha": fecha_a_texto(row[col_fecha]) if col_fecha else "",
            "concepto": concepto.upper(),
            "area": limpiar_texto(row[col_area]).upper() if col_area else "",
            "solicitante": "",
            "proveedor": limpiar_texto(row[col_proveedor]).upper() if col_proveedor else "",
            "factura": limpiar_texto(row[col_factura]).upper() if col_factura else "",
            "fecha_factura": "",
            "importe": limpiar_importe(row[col_importe]) if col_importe else 0.0,
            "cargado_a": limpiar_texto(row[col_cargado]).upper() if col_cargado else "",
            "estatus": "Capturada",
            "observaciones": "IMPORTADO DESDE EXCEL"
        }

        antes = len(obtener_df_requisiciones())
        insertar_requisicion(datos)
        despues = len(obtener_df_requisiciones())
        if despues > antes:
            total_insertados += 1

    return total_insertados, len(df)

# ============================================================
# ENCABEZADO
# ============================================================
st.markdown("""
<div class="header-card">
    <h1>🛒 Módulo de Adquisiciones</h1>
    <p>Requisiciones · Firmas · Cotizaciones · Compras · Evidencias · Entregas</p>
</div>
""", unsafe_allow_html=True)

st.sidebar.success(f"Usuario: {st.session_state.usuario_adq} | Rol: {st.session_state.rol_adq}")

if st.sidebar.button("Cerrar sesión"):
    st.session_state.logueado_adq = False
    st.session_state.usuario_adq = ""
    st.session_state.rol_adq = ""
    st.rerun()

menu = st.sidebar.radio(
    "Menú",
    [
        "🏠 Inicio",
        "📤 Importar Excel",
        "➕ Nueva requisición",
        "📋 Requisiciones",
        "📎 Documentos y evidencias",
        "🏢 Catálogo de áreas",
        "🏪 Catálogo de proveedores",
        "📊 Reportes",
    ]
)

df_reqs = obtener_df_requisiciones()

# ============================================================
# INICIO
# ============================================================
if menu == "🏠 Inicio":
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.subheader("Resumen general")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Requisiciones", len(df_reqs))
    c2.metric("Áreas", df_reqs["area"].nunique() if not df_reqs.empty else 0)
    c3.metric("Proveedores", df_reqs["proveedor"].nunique() if not df_reqs.empty else 0)
    c4.metric("Importe total", f"${df_reqs['importe'].sum():,.2f}" if not df_reqs.empty else "$0.00")

    st.markdown("</div>", unsafe_allow_html=True)

    if df_reqs.empty:
        st.warning("Todavía no hay requisiciones. Puedes iniciar importando tu Excel 2025.")
    else:
        st.subheader("Últimas requisiciones")
        st.dataframe(
            df_reqs[["id", "folio", "fecha", "area", "concepto", "proveedor", "importe", "estatus"]].head(25),
            use_container_width=True
        )

# ============================================================
# IMPORTAR EXCEL
# ============================================================
elif menu == "📤 Importar Excel":
    if st.session_state.rol_adq == "Consulta":
        st.warning("Tu usuario solo tiene permiso de consulta.")
        st.stop()

    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.subheader("Importar Excel de requisiciones")

    archivo_excel = st.file_uploader("Subir archivo Excel", type=["xlsx", "xls"])

    if st.button("📤 Importar requisiciones"):
        if archivo_excel is None:
            st.error("Primero sube un archivo Excel.")
        else:
            insertados, total = importar_excel(archivo_excel)
            st.success(f"Importación terminada. Nuevas requisiciones: {insertados} de {total} filas leídas.")
            st.rerun()

    st.info("El sistema detecta columnas como: FECHA, # REQUI, CONCEPTO, AREA, PROVEEDOR, FACT, IMPORTE y CARGADO A.")
    st.markdown("</div>", unsafe_allow_html=True)

# ============================================================
# NUEVA REQUISICIÓN
# ============================================================
elif menu == "➕ Nueva requisición":
    if st.session_state.rol_adq == "Consulta":
        st.warning("Tu usuario solo tiene permiso de consulta.")
        st.stop()

    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.subheader("Nueva requisición")

    areas = obtener_areas()
    proveedores = obtener_proveedores()

    col1, col2 = st.columns(2)

    with col1:
        folio = st.text_input("Folio", value=generar_folio())
        fecha_req = st.date_input("Fecha", value=date.today())
        area_opcion = st.selectbox("Área solicitante", ["-- Nueva área --"] + areas)
        if area_opcion == "-- Nueva área --":
            area = st.text_input("Escribe el área").upper()
        else:
            area = area_opcion

        solicitante = st.text_input("Solicitante")

    with col2:
        proveedor_opcion = st.selectbox("Proveedor", ["-- Sin proveedor / Nuevo --"] + proveedores)
        if proveedor_opcion == "-- Sin proveedor / Nuevo --":
            proveedor = st.text_input("Escribe el proveedor").upper()
        else:
            proveedor = proveedor_opcion

        factura = st.text_input("Factura")
        importe = st.number_input("Importe", min_value=0.0, step=100.0)
        cargado_a = st.text_input("Cargado a / Programa")

    concepto = st.text_area("Concepto / descripción de la requisición")
    observaciones = st.text_area("Observaciones")
    estatus = st.selectbox("Estatus inicial", ESTATUS, index=0)

    if st.button("💾 Guardar requisición"):
        if not folio.strip():
            st.error("El folio es obligatorio.")
        elif not concepto.strip():
            st.error("El concepto es obligatorio.")
        else:
            datos = {
                "folio": folio.upper(),
                "fecha": str(fecha_req),
                "concepto": concepto.upper(),
                "area": area.upper(),
                "solicitante": solicitante.upper(),
                "proveedor": proveedor.upper(),
                "factura": factura.upper(),
                "fecha_factura": "",
                "importe": importe,
                "cargado_a": cargado_a.upper(),
                "estatus": estatus,
                "observaciones": observaciones.upper()
            }
            insertar_requisicion(datos)
            st.success("Requisición guardada correctamente.")
            st.rerun()

    st.markdown("</div>", unsafe_allow_html=True)

# ============================================================
# REQUISICIONES
# ============================================================
elif menu == "📋 Requisiciones":
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.subheader("Consulta y seguimiento de requisiciones")

    if df_reqs.empty:
        st.warning("No hay requisiciones capturadas.")
        st.stop()

    col1, col2, col3 = st.columns(3)

    with col1:
        texto = st.text_input("Buscar por folio, concepto, proveedor o factura")
    with col2:
        area_filtro = st.selectbox("Área", ["Todas"] + sorted([x for x in df_reqs["area"].dropna().unique() if x]))
    with col3:
        estatus_filtro = st.selectbox("Estatus", ["Todos"] + ESTATUS)

    df_filtrado = df_reqs.copy()

    if texto:
        t = texto.upper()
        filtro = (
            df_filtrado["folio"].astype(str).str.upper().str.contains(t, na=False) |
            df_filtrado["concepto"].astype(str).str.upper().str.contains(t, na=False) |
            df_filtrado["proveedor"].astype(str).str.upper().str.contains(t, na=False) |
            df_filtrado["factura"].astype(str).str.upper().str.contains(t, na=False)
        )
        df_filtrado = df_filtrado[filtro]

    if area_filtro != "Todas":
        df_filtrado = df_filtrado[df_filtrado["area"] == area_filtro]

    if estatus_filtro != "Todos":
        df_filtrado = df_filtrado[df_filtrado["estatus"] == estatus_filtro]

    st.write(f"Resultados: **{len(df_filtrado)}**")
    st.dataframe(
        df_filtrado[["id", "folio", "fecha", "area", "concepto", "proveedor", "factura", "importe", "estatus"]],
        use_container_width=True
    )

    if not df_filtrado.empty:
        st.markdown("### Actualizar estatus")
        req_id = st.selectbox("Selecciona ID", df_filtrado["id"].tolist())
        fila = df_filtrado[df_filtrado["id"] == req_id].iloc[0]

        st.write(f"**Folio:** {fila['folio']}")
        st.write(f"**Concepto:** {fila['concepto']}")
        st.write(f"**Estatus actual:** {fila['estatus']}")

        nuevo_estatus = st.selectbox("Nuevo estatus", ESTATUS, index=ESTATUS.index(fila["estatus"]) if fila["estatus"] in ESTATUS else 0)
        obs = st.text_area("Observación del cambio")

        if st.button("🔄 Actualizar estatus"):
            actualizar_estatus(req_id, nuevo_estatus, obs.upper())
            st.success("Estatus actualizado.")
            st.rerun()

    st.download_button(
        "📥 Descargar requisiciones filtradas",
        data=crear_excel(df_filtrado),
        file_name="requisiciones_filtradas.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

    st.markdown("</div>", unsafe_allow_html=True)

# ============================================================
# DOCUMENTOS
# ============================================================
elif menu == "📎 Documentos y evidencias":
    if st.session_state.rol_adq == "Consulta":
        st.warning("Tu usuario solo tiene permiso de consulta.")
        st.stop()

    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.subheader("Subir documentos y evidencias")

    if df_reqs.empty:
        st.warning("No hay requisiciones capturadas.")
        st.stop()

    req_id = st.selectbox(
        "Selecciona requisición",
        df_reqs["id"].tolist(),
        format_func=lambda x: f"{df_reqs[df_reqs['id']==x].iloc[0]['folio']} - {df_reqs[df_reqs['id']==x].iloc[0]['concepto'][:60]}"
    )

    fila = df_reqs[df_reqs["id"] == req_id].iloc[0]
    st.write(f"**Folio:** {fila['folio']}")
    st.write(f"**Área:** {fila['area']}")
    st.write(f"**Estatus:** {fila['estatus']}")

    tipo_doc = st.selectbox("Tipo de documento", TIPOS_DOCUMENTO)
    archivo = st.file_uploader("Subir archivo", type=["pdf", "jpg", "jpeg", "png", "xml", "xlsx", "docx"])

    if st.button("📎 Guardar documento"):
        if archivo is None:
            st.error("Selecciona un archivo.")
        else:
            ruta = subir_documento(req_id, fila["folio"], tipo_doc, archivo)
            st.success(f"Documento guardado: {ruta}")

            if tipo_doc == "Evidencia de compra":
                actualizar_estatus(req_id, "Evidencia cargada", "Evidencia de compra cargada.")
            elif tipo_doc == "Firma de recibido":
                actualizar_estatus(req_id, "Firmado recibido", "Firma de recibido cargada.")
            elif tipo_doc == "Requisición firmada":
                actualizar_estatus(req_id, "Firmada", "Requisición firmada cargada.")
            st.rerun()

    st.markdown("### Documentos cargados")
    docs = obtener_documentos(req_id)
    if docs.empty:
        st.info("No hay documentos cargados para esta requisición.")
    else:
        st.dataframe(docs[["tipo_documento", "nombre_archivo", "fecha_subida", "usuario"]], use_container_width=True)

    st.markdown("</div>", unsafe_allow_html=True)

# ============================================================
# CATÁLOGO ÁREAS
# ============================================================
elif menu == "🏢 Catálogo de áreas":
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.subheader("Catálogo de áreas")

    nueva_area = st.text_input("Nueva área")
    if st.button("➕ Agregar área"):
        agregar_area(nueva_area)
        st.success("Área agregada.")
        st.rerun()

    areas = obtener_areas()
    st.dataframe(pd.DataFrame({"Áreas activas": areas}), use_container_width=True)
    st.markdown("</div>", unsafe_allow_html=True)

# ============================================================
# CATÁLOGO PROVEEDORES
# ============================================================
elif menu == "🏪 Catálogo de proveedores":
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.subheader("Catálogo de proveedores")

    nuevo_proveedor = st.text_input("Nuevo proveedor")
    if st.button("➕ Agregar proveedor"):
        agregar_proveedor(nuevo_proveedor)
        st.success("Proveedor agregado.")
        st.rerun()

    proveedores = obtener_proveedores()
    st.dataframe(pd.DataFrame({"Proveedores activos": proveedores}), use_container_width=True)
    st.markdown("</div>", unsafe_allow_html=True)

# ============================================================
# REPORTES
# ============================================================
elif menu == "📊 Reportes":
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.subheader("Reportes de adquisiciones")

    if df_reqs.empty:
        st.warning("No hay información para reportar.")
        st.stop()

    col1, col2 = st.columns(2)
    with col1:
        area = st.selectbox("Filtrar área", ["Todas"] + sorted([x for x in df_reqs["area"].dropna().unique() if x]))
    with col2:
        estatus = st.selectbox("Filtrar estatus", ["Todos"] + ESTATUS)

    rep = df_reqs.copy()
    if area != "Todas":
        rep = rep[rep["area"] == area]
    if estatus != "Todos":
        rep = rep[rep["estatus"] == estatus]

    c1, c2, c3 = st.columns(3)
    c1.metric("Requisiciones", len(rep))
    c2.metric("Importe total", f"${rep['importe'].sum():,.2f}")
    c3.metric("Proveedores", rep["proveedor"].nunique())

    st.markdown("### Compras por área")
    por_area = rep.groupby("area", dropna=False)["importe"].sum().reset_index().sort_values("importe", ascending=False)
    st.dataframe(por_area, use_container_width=True)
    if not por_area.empty:
        st.bar_chart(por_area.set_index("area"))

    st.markdown("### Requisiciones por estatus")
    por_estatus = rep["estatus"].value_counts().reset_index()
    por_estatus.columns = ["estatus", "total"]
    st.dataframe(por_estatus, use_container_width=True)

    st.download_button(
        "📥 Descargar reporte Excel",
        data=crear_excel(rep),
        file_name="reporte_adquisiciones.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

    st.markdown("</div>", unsafe_allow_html=True)
