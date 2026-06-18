
import os
import re
import sqlite3
from datetime import datetime, date
from io import BytesIO
from pathlib import Path

import pandas as pd
import streamlit as st
import firebase_admin
from firebase_admin import credentials, firestore, storage

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

# Colecciones en Firestore
COLECCION_REQUISICIONES = "adquisiciones_requisiciones"
COLECCION_AREAS = "adquisiciones_areas"
COLECCION_PROVEEDORES = "adquisiciones_proveedores"
COLECCION_DOCUMENTOS = "adquisiciones_documentos"

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
# FIREBASE / FIRESTORE / STORAGE
# ============================================================
@st.cache_resource
def conectar_firebase():
    """
    Usa el mismo bloque [firebase] que ya tienes en Streamlit Secrets.
    Si tienes Storage activado, agrega también:
    storage_bucket = "NOMBRE_REAL_DEL_BUCKET"
    """
    try:
        if not firebase_admin._apps:
            if "firebase" not in st.secrets:
                return None, None, "No se encontró [firebase] en Streamlit Secrets."

            fb = dict(st.secrets["firebase"])
            bucket_name = fb.get("storage_bucket", "")

            if bucket_name:
                firebase_admin.initialize_app(
                    credentials.Certificate(fb),
                    {"storageBucket": bucket_name}
                )
            else:
                firebase_admin.initialize_app(credentials.Certificate(fb))

        db = firestore.client()

        try:
            bucket = storage.bucket()
        except Exception:
            bucket = None

        return db, bucket, None

    except Exception as e:
        return None, None, str(e)


db_firebase, bucket_firebase, error_firebase = conectar_firebase()


def firebase_disponible():
    return db_firebase is not None


def storage_disponible():
    return bucket_firebase is not None


def limpiar_id_firestore(texto):
    texto = str(texto).strip()
    texto = texto.replace("/", "-").replace("\\", "-").replace("#", "-")
    texto = texto.replace("[", "").replace("]", "").replace("*", "")
    return texto if texto else ""


def guardar_requisicion_firestore(datos):
    """Guarda o actualiza una requisición en Firestore."""
    if not firebase_disponible():
        return ""

    try:
        folio = limpiar_id_firestore(datos.get("folio", ""))
        if folio:
            ref = db_firebase.collection(COLECCION_REQUISICIONES).document(folio)
        else:
            ref = db_firebase.collection(COLECCION_REQUISICIONES).document()

        datos_fb = dict(datos)
        datos_fb["firebase_id"] = ref.id
        datos_fb["fecha_actualizacion"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ref.set(datos_fb, merge=True)
        return ref.id

    except Exception as e:
        st.warning(f"No se pudo guardar en Firestore: {e}")
        return ""


def guardar_area_firestore(nombre):
    if not firebase_disponible() or not nombre:
        return

    try:
        doc_id = limpiar_id_firestore(nombre.upper())
        db_firebase.collection(COLECCION_AREAS).document(doc_id).set({
            "nombre": nombre.upper(),
            "activa": True,
            "fecha_registro": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }, merge=True)
    except Exception:
        pass


def guardar_proveedor_firestore(nombre):
    if not firebase_disponible() or not nombre:
        return

    try:
        doc_id = limpiar_id_firestore(nombre.upper())
        db_firebase.collection(COLECCION_PROVEEDORES).document(doc_id).set({
            "nombre": nombre.upper(),
            "activo": True,
            "fecha_registro": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }, merge=True)
    except Exception:
        pass


def actualizar_estatus(req_id, nuevo_estatus, observaciones_extra=""):
    df_actual = obtener_df_requisiciones()
    fila = df_actual[df_actual["id"] == req_id].iloc[0]
    folio = fila["folio"]

    obs_txt = f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M')}] {observaciones_extra}" if observaciones_extra else ""

    con = conectar()
    cur = con.cursor()
    cur.execute(
        "UPDATE requisiciones SET estatus = ?, observaciones = COALESCE(observaciones,'') || ? WHERE folio = ?",
        (nuevo_estatus, obs_txt, folio)
    )
    con.commit()
    con.close()

    actualizar_estatus_firestore(folio, nuevo_estatus, observaciones_extra)
    leer_requisiciones_firestore.clear()

def subir_documento(req_id, folio, tipo_documento, archivo):
    if archivo is None:
        return ""

    carpeta_folio = DOCS_DIR / normalizar_nombre_archivo(folio)
    carpeta_folio.mkdir(parents=True, exist_ok=True)

    extension = Path(archivo.name).suffix.lower()
    fecha = datetime.now().strftime("%Y%m%d_%H%M%S")
    nombre_archivo = f"{fecha}_{normalizar_nombre_archivo(tipo_documento)}{extension}"
    ruta = carpeta_folio / nombre_archivo

    archivo.seek(0)
    with open(ruta, "wb") as f:
        f.write(archivo.read())

    archivo_url, archivo_storage_path = subir_archivo_storage(archivo, folio, tipo_documento)

    datos_doc = {
        "requisicion_id": req_id,
        "folio": folio,
        "tipo_documento": tipo_documento,
        "nombre_archivo": nombre_archivo,
        "ruta_archivo": str(ruta),
        "archivo_url": archivo_url,
        "archivo_storage_path": archivo_storage_path,
        "fecha_subida": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "usuario": st.session_state.usuario_adq
    }

    firebase_id = guardar_documento_firestore(datos_doc)

    con = conectar()
    cur = con.cursor()
    cur.execute("""
        INSERT INTO documentos (
            requisicion_id, folio, tipo_documento, nombre_archivo,
            ruta_archivo, archivo_url, archivo_storage_path, fecha_subida, usuario, firebase_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        req_id,
        folio,
        tipo_documento,
        nombre_archivo,
        str(ruta),
        archivo_url,
        archivo_storage_path,
        datos_doc["fecha_subida"],
        st.session_state.usuario_adq,
        firebase_id
    ))
    con.commit()
    con.close()

    return str(ruta)

def obtener_documentos(req_id, folio=""):
    df_fb = leer_documentos_firestore(folio)
    if not df_fb.empty:
        return df_fb

    con = conectar()
    df = pd.read_sql_query(
        "SELECT * FROM documentos WHERE requisicion_id = ? OR folio = ? ORDER BY id DESC",
        con,
        params=(req_id, folio)
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
    df = pd.read_excel(uploaded_file, sheet_name="REQUISICIONES 2025", header=1)

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

        antes = len(obtener_df_requisiciones_local())
        insertar_requisicion(datos)
        despues = len(obtener_df_requisiciones_local())
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

if firebase_disponible():
    st.sidebar.success("Firestore conectado")
else:
    st.sidebar.warning("Firestore no conectado")
    with st.sidebar.expander("Ver error Firebase"):
        st.write(error_firebase)

if storage_disponible():
    st.sidebar.success("Storage configurado")
else:
    st.sidebar.info("Storage no configurado")

if st.sidebar.button("🔄 Actualizar nube"):
    leer_requisiciones_firestore.clear()
    st.rerun()

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
            firebase_id = insertar_requisicion(datos)
            if firebase_id:
                st.success("Requisición guardada en Firestore correctamente.")
            else:
                st.warning("Requisición guardada localmente. Firestore no respondió.")
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
    docs = obtener_documentos(req_id, fila["folio"])
    if docs.empty:
        st.info("No hay documentos cargados para esta requisición.")
    else:
        columnas_docs = [c for c in ["tipo_documento", "nombre_archivo", "archivo_url", "fecha_subida", "usuario"] if c in docs.columns]
        st.dataframe(docs[columnas_docs], use_container_width=True)

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
