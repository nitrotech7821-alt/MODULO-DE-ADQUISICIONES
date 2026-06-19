import re
import time
from datetime import datetime, date
from io import BytesIO
from pathlib import Path

import pandas as pd
import streamlit as st
import firebase_admin
from firebase_admin import credentials, firestore, storage

# ============================================================
# CONFIGURACIÓN
# ============================================================
st.set_page_config(
    page_title="Sistema de Adquisiciones DIF",
    page_icon="🛒",
    layout="wide"
)

COLECCION_REQUISICIONES = "requisiciones"
COLECCION_DOCUMENTOS = "adquisiciones_documentos"
BATCH_SIZE = 100

TIPOS_DOCUMENTO = [
    "Requisición firmada",
    "Cotización",
    "Factura PDF",
    "XML",
    "Evidencia de compra",
    "Evidencia de entrega",
    "Firma de recibido",
    "Foto",
    "Otro",
]

USUARIOS = {
    "admin": {"password": "1234", "rol": "Administrador"},
    "adquisiciones": {"password": "1234", "rol": "Adquisiciones"},
    "consulta": {"password": "1234", "rol": "Consulta"},
}

ESTATUS = [
    "Importada", "Capturada", "En firma", "Firmada", "En cotización",
    "Compra realizada", "Producto recibido", "Evidencia cargada",
    "Entregado", "Firmado recibido", "Cerrada", "Cancelada",
]

# ============================================================
# ESTILO
# ============================================================
st.markdown("""
<style>
.stApp {
    background:
        radial-gradient(circle at top left, rgba(8,123,117,0.15), transparent 30%),
        radial-gradient(circle at bottom right, rgba(233,78,27,0.16), transparent 35%),
        linear-gradient(135deg, #EEF8F5 0%, #FFF7E7 55%, #FDE0CF 100%);
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
.header-card h1 { color: #087B75; font-weight: 900; }
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
# FIREBASE
# ============================================================
@st.cache_resource
def conectar_firebase():
    try:
        if not firebase_admin._apps:
            if "firebase" not in st.secrets:
                return None, None, "No se encontró [firebase] en Secrets."

            fb = dict(st.secrets["firebase"])
            cred = credentials.Certificate(fb)
            bucket_name = str(fb.get("storage_bucket", "")).strip()

            if bucket_name:
                firebase_admin.initialize_app(cred, {"storageBucket": bucket_name})
            else:
                firebase_admin.initialize_app(cred)

        db_cliente = firestore.client()

        try:
            bucket_cliente = storage.bucket()
        except Exception:
            bucket_cliente = None

        return db_cliente, bucket_cliente, None
    except Exception as e:
        return None, None, str(e)

db, bucket, error_firebase = conectar_firebase()

def firebase_ok():
    return db is not None

def storage_ok():
    return bucket is not None

# ============================================================
# UTILIDADES
# ============================================================
def limpiar_texto(valor):
    if pd.isna(valor):
        return ""
    return str(valor).strip()

def limpiar_folio(valor):
    texto = limpiar_texto(valor).replace(".0", "").strip()
    texto = re.sub(r"[^A-Za-z0-9\-_]", "-", texto)
    return texto

def limpiar_importe(valor):
    if pd.isna(valor) or valor == "":
        return 0.0
    texto = str(valor).replace("$", "").replace(",", "").strip()
    try:
        return float(texto)
    except Exception:
        return 0.0

def fecha_texto(valor):
    if pd.isna(valor) or valor == "":
        return ""
    try:
        return pd.to_datetime(valor).strftime("%Y-%m-%d")
    except Exception:
        return str(valor)

def encontrar_columna(df, opciones):
    columnas = {str(c).strip().upper(): c for c in df.columns}
    for opcion in opciones:
        opcion = opcion.upper()
        for col_upper, col_original in columnas.items():
            if col_upper == opcion or opcion in col_upper:
                return col_original
    return None

def crear_excel(df):
    salida = BytesIO()
    df.to_excel(salida, index=False)
    salida.seek(0)
    return salida

def doc_id_requisicion(registro):
    folio = limpiar_folio(registro.get("folio", ""))
    anio = str(registro.get("anio", "")).strip()
    if anio:
        return f"{anio}-{folio}"
    return folio if folio else f"AUTO-{datetime.now().strftime('%Y%m%d%H%M%S')}"

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
    return texto[:90] if texto else "ARCHIVO"

def folio_a_doc_id(folio, anio=""):
    folio = limpiar_folio(folio)
    anio = str(anio).strip()
    if anio:
        return f"{anio}-{folio}"
    return folio

def subir_archivo_storage(archivo, folio, tipo_documento):
    if archivo is None:
        return "", ""

    if not storage_ok():
        return "", ""

    try:
        extension = Path(archivo.name).suffix.lower()
        fecha = datetime.now().strftime("%Y%m%d_%H%M%S")
        folio_limpio = normalizar_nombre_archivo(folio)
        tipo_limpio = normalizar_nombre_archivo(tipo_documento)
        nombre_limpio = normalizar_nombre_archivo(Path(archivo.name).stem)
        storage_path = f"adquisiciones/{folio_limpio}/{fecha}_{tipo_limpio}_{nombre_limpio}{extension}"

        blob = bucket.blob(storage_path)
        archivo.seek(0)
        blob.upload_from_string(
            archivo.read(),
            content_type=getattr(archivo, "type", "application/octet-stream")
        )

        # URL directa. Si las reglas de Storage no son públicas, se conserva la ruta interna.
        return blob.public_url, storage_path

    except Exception as e:
        st.warning(f"No se pudo subir a Firebase Storage: {e}")
        return "", ""

@st.cache_data(ttl=60, show_spinner=False)
def leer_documentos_firestore(folio):
    if not firebase_ok() or not folio:
        return pd.DataFrame()

    rows = []
    try:
        docs = db.collection(COLECCION_DOCUMENTOS).where("folio", "==", str(folio)).stream(timeout=60)
        for doc in docs:
            d = doc.to_dict()
            d["firebase_id"] = doc.id
            rows.append(d)
    except Exception as e:
        st.warning(f"No se pudieron leer documentos: {e}")
        return pd.DataFrame()

    return pd.DataFrame(rows)

def guardar_documento_firestore(datos):
    if not firebase_ok():
        st.error("Firebase no está conectado.")
        return False

    try:
        ref = db.collection(COLECCION_DOCUMENTOS).document()
        datos = dict(datos)
        datos["firebase_id"] = ref.id
        datos["fecha_subida"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        datos["usuario"] = st.session_state.usuario_adq
        ref.set(datos, merge=True)
        leer_documentos_firestore.clear()
        return True
    except Exception as e:
        st.error(f"No se pudo guardar el documento en Firestore: {e}")
        return False

# ============================================================
# LOGIN
# ============================================================
def login():
    if "logueado_adq" not in st.session_state:
        st.session_state.logueado_adq = False
    if "usuario_adq" not in st.session_state:
        st.session_state.usuario_adq = ""
    if "rol_adq" not in st.session_state:
        st.session_state.rol_adq = ""

    if st.session_state.logueado_adq:
        return True

    st.markdown("""
    <div class="header-card">
        <h1>🛒 Sistema de Adquisiciones DIF</h1>
        <p>Requisiciones · Compras · Evidencias · Reportes</p>
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

# ============================================================
# FIRESTORE
# ============================================================
@st.cache_data(ttl=120, show_spinner=False)
def leer_requisiciones_firestore():
    if not firebase_ok():
        return pd.DataFrame()
    rows = []
    try:
        docs = db.collection(COLECCION_REQUISICIONES).stream(timeout=120)
        for doc in docs:
            d = doc.to_dict()
            d["firebase_id"] = doc.id
            rows.append(d)
    except Exception as e:
        st.warning(f"No se pudo leer Firestore: {e}")
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    columnas = [
        "firebase_id", "folio", "fecha", "anio", "mes", "concepto", "area",
        "proveedor", "factura", "fecha_factura", "importe", "cargado_a",
        "estatus", "origen", "fecha_importacion", "fecha_captura", "usuario"
    ]
    for col in columnas:
        if col not in df.columns:
            df[col] = ""

    df["importe"] = pd.to_numeric(df["importe"], errors="coerce").fillna(0)
    if "fecha" in df.columns:
        df = df.sort_values("fecha", ascending=False, na_position="last")
    df["id"] = range(1, len(df) + 1)
    return df

@st.cache_data(ttl=60, show_spinner=False)
def buscar_requisicion_por_folio(folio_buscar):
    """
    Busca una sola requisición por folio, sin leer las 10,449 requisiciones.
    Esto evita el error 429 Quota exceeded.
    """
    if not firebase_ok():
        return pd.DataFrame()

    folio_buscar = str(folio_buscar).strip()
    if not folio_buscar:
        return pd.DataFrame()

    resultados = []
    posibles_ids = [
        folio_buscar,
        f"2025-{limpiar_folio(folio_buscar)}",
        f"2026-{limpiar_folio(folio_buscar)}",
    ]

    try:
        # Primero busca por ID directo
        for doc_id in posibles_ids:
            doc = db.collection(COLECCION_REQUISICIONES).document(doc_id).get()
            if doc.exists:
                d = doc.to_dict()
                d["firebase_id"] = doc.id
                resultados.append(d)

        # Luego busca por campo folio
        consulta = (
            db.collection(COLECCION_REQUISICIONES)
            .where("folio", "==", folio_buscar)
            .limit(10)
            .stream(timeout=30)
        )

        for doc in consulta:
            d = doc.to_dict()
            d["firebase_id"] = doc.id
            if not any(x.get("firebase_id") == doc.id for x in resultados):
                resultados.append(d)

    except Exception as e:
        st.warning(f"No se pudo buscar el folio: {e}")
        return pd.DataFrame()

    df = pd.DataFrame(resultados)
    if df.empty:
        return df

    columnas = [
        "firebase_id", "folio", "fecha", "anio", "mes", "concepto", "area",
        "proveedor", "factura", "fecha_factura", "importe", "cargado_a",
        "estatus", "origen", "fecha_importacion", "fecha_captura", "usuario"
    ]

    for col in columnas:
        if col not in df.columns:
            df[col] = ""

    df["importe"] = pd.to_numeric(df["importe"], errors="coerce").fillna(0)
    df["id"] = range(1, len(df) + 1)

    return df


def guardar_requisicion(datos):
    if not firebase_ok():
        st.error("Firebase no está conectado.")
        return False
    datos = dict(datos)
    datos["fecha_actualizacion"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    doc_id = doc_id_requisicion(datos)
    try:
        db.collection(COLECCION_REQUISICIONES).document(doc_id).set(datos, merge=True)
        leer_requisiciones_firestore.clear()
        return True
    except Exception as e:
        st.error(f"No se pudo guardar en Firestore: {e}")
        return False

def actualizar_estatus_firestore(firebase_id, nuevo_estatus, observacion):
    try:
        db.collection(COLECCION_REQUISICIONES).document(firebase_id).set({
            "estatus": nuevo_estatus,
            "ultima_observacion": observacion,
            "fecha_actualizacion": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "usuario_actualizacion": st.session_state.usuario_adq,
        }, merge=True)
        leer_requisiciones_firestore.clear()
        return True
    except Exception as e:
        st.error(f"No se pudo actualizar: {e}")
        return False

# ============================================================
# IMPORTADOR
# ============================================================
def leer_hoja_inteligente(archivo, hoja):
    mejor_df = None
    mejor_header = 0
    mejor_score = -1
    for header in range(0, 8):
        try:
            archivo.seek(0)
            df_tmp = pd.read_excel(archivo, sheet_name=hoja, header=header)
            columnas = " ".join([str(c).upper() for c in df_tmp.columns])
            score = sum(1 for palabra in ["FECHA", "REQUI", "CONCEPTO", "AREA", "PROVEEDOR", "IMPORTE"] if palabra in columnas)
            if score > mejor_score:
                mejor_df = df_tmp
                mejor_header = header
                mejor_score = score
        except Exception:
            pass
    return mejor_df, mejor_header

def preparar_requisiciones(archivo, hoja):
    df, header = leer_hoja_inteligente(archivo, hoja)
    if df is None or df.empty:
        return pd.DataFrame(), header

    col_fecha = encontrar_columna(df, ["FECHA"])
    col_folio = encontrar_columna(df, ["# REQUI", "REQUI", "FOLIO"])
    col_concepto = encontrar_columna(df, ["CONCEPTO", "DESCRIPCION", "DESCRIPCIÓN"])
    col_area = encontrar_columna(df, ["AREA", "ÁREA"])
    col_proveedor = encontrar_columna(df, ["PROVEEDOR"])
    col_factura = encontrar_columna(df, ["FACT", "FACTURA"])
    col_fecha_fact = encontrar_columna(df, ["FECHA FACT", "FECHA_FACT", "FECHA FACTURA"])
    col_importe = encontrar_columna(df, ["IMPORTE", "TOTAL", "MONTO"])
    col_cargado = encontrar_columna(df, ["CARGADO A", "CARGADO"])

    registros = []
    for i, row in df.iterrows():
        folio = limpiar_folio(row[col_folio]) if col_folio else ""
        concepto = limpiar_texto(row[col_concepto]).upper() if col_concepto else ""
        if not folio and not concepto:
            continue
        if not folio:
            folio = f"SIN-FOLIO-{hoja}-{i+1}"
        fecha = fecha_texto(row[col_fecha]) if col_fecha else ""
        anio = ""
        mes = ""
        if fecha:
            try:
                f = pd.to_datetime(fecha)
                anio = str(f.year)
                mes = f"{f.month:02d}"
            except Exception:
                pass
        registros.append({
            "folio": str(folio),
            "fecha": fecha,
            "anio": anio,
            "mes": mes,
            "concepto": concepto,
            "area": limpiar_texto(row[col_area]).upper() if col_area else "",
            "proveedor": limpiar_texto(row[col_proveedor]).upper() if col_proveedor else "",
            "factura": limpiar_texto(row[col_factura]).upper() if col_factura else "",
            "fecha_factura": fecha_texto(row[col_fecha_fact]) if col_fecha_fact else "",
            "importe": limpiar_importe(row[col_importe]) if col_importe else 0.0,
            "cargado_a": limpiar_texto(row[col_cargado]).upper() if col_cargado else "",
            "estatus": "Importada",
            "origen": hoja,
            "fecha_importacion": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
    return pd.DataFrame(registros), header

def subir_lote_firestore(registros, reintentos=3):
    ultimo_error = ""
    for intento in range(1, reintentos + 1):
        try:
            batch = db.batch()
            contador = 0
            for registro in registros:
                doc_id = doc_id_requisicion(registro)
                ref = db.collection(COLECCION_REQUISICIONES).document(doc_id)
                batch.set(ref, registro, merge=True)
                contador += 1
            if contador > 0:
                batch.commit()
            return contador, ""
        except Exception as e:
            ultimo_error = str(e)
            time.sleep(2 * intento)
    return 0, ultimo_error

# ============================================================
# ENCABEZADO Y SIDEBAR
# ============================================================
st.markdown("""
<div class="header-card">
    <h1>🛒 Módulo de Adquisiciones</h1>
    <p>Requisiciones · Firmas · Cotizaciones · Compras · Evidencias · Entregas</p>
</div>
""", unsafe_allow_html=True)

st.sidebar.success(f"Usuario: {st.session_state.usuario_adq} | Rol: {st.session_state.rol_adq}")
if firebase_ok():
    st.sidebar.success("Firebase conectado")
else:
    st.sidebar.error("Firebase no conectado")
    with st.sidebar.expander("Ver error"):
        st.write(error_firebase)

if storage_ok():
    st.sidebar.success("Storage conectado")
else:
    st.sidebar.warning("Storage no configurado")

if st.sidebar.button("🔄 Actualizar datos"):
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
        "📋 Requisiciones",
        "➕ Nueva requisición",
        "📎 Documentos y evidencias",
        "📤 Importador histórico",
        "📊 Reportes",
    ]
)

# Evita leer toda la colección cuando no es necesario.
# Con más de 10 mil registros, leer todo en cada pantalla puede generar 429 Quota exceeded.
if menu in ["🏠 Inicio", "📋 Requisiciones", "📊 Reportes"]:
    df_reqs = leer_requisiciones_firestore()
else:
    df_reqs = pd.DataFrame()

# ============================================================
# INICIO
# ============================================================
if menu == "🏠 Inicio":
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.subheader("Resumen general")

    st.success("Sistema conectado a Firebase y Storage.")
    st.info("Para evitar el error 429 de Firestore, esta pantalla ya no carga las 10,449 requisiciones automáticamente.")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Base", "Firestore")
    c2.metric("Registros históricos", "10,449")
    c3.metric("Modo", "Optimizado")
    c4.metric("Storage", "Activo" if storage_ok() else "No configurado")

    st.markdown("### Accesos rápidos")
    st.write("Usa **Requisiciones** para buscar por folio.")
    st.write("Usa **Documentos y evidencias** para subir factura, XML, cotización o evidencia por folio.")
    st.write("Usa **Nueva requisición** para capturar una nueva requisición.")

    st.markdown("</div>", unsafe_allow_html=True)

# ============================================================
# REQUISICIONES
# ============================================================
elif menu == "📋 Requisiciones":
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.subheader("Consulta y seguimiento de requisiciones")
    st.info("Busca por folio para no leer toda la colección de Firestore.")

    folio_buscar = st.text_input(
        "Folio de requisición",
        placeholder="Ejemplo: 7050, 2025-7050, REQ-2026-00001"
    )

    if st.button("🔎 Buscar requisición"):
        buscar_requisicion_por_folio.clear()
        st.session_state["df_req_busqueda"] = buscar_requisicion_por_folio(folio_buscar)

    if "df_req_busqueda" not in st.session_state:
        st.session_state["df_req_busqueda"] = pd.DataFrame()

    df_filtrado = st.session_state["df_req_busqueda"]

    if df_filtrado.empty:
        st.warning("Escribe un folio y presiona Buscar requisición.")
        st.markdown("</div>", unsafe_allow_html=True)
        st.stop()

    st.write(f"Resultados: **{len(df_filtrado):,}**")

    cols = ["id", "folio", "fecha", "area", "concepto", "proveedor", "factura", "importe", "estatus"]
    st.dataframe(df_filtrado[[c for c in cols if c in df_filtrado.columns]], use_container_width=True)

    st.download_button(
        "📥 Descargar resultado Excel",
        data=crear_excel(df_filtrado),
        file_name="requisicion_consultada.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

    if st.session_state.rol_adq != "Consulta" and not df_filtrado.empty:
        st.markdown("### Actualizar estatus")
        req_id = st.selectbox("Selecciona ID", df_filtrado["id"].tolist())
        fila = df_filtrado[df_filtrado["id"] == req_id].iloc[0]

        st.write(f"**Folio:** {fila['folio']}")
        st.write(f"**Concepto:** {fila['concepto']}")

        nuevo_estatus = st.selectbox("Nuevo estatus", ESTATUS, index=0)
        observacion = st.text_area("Observación")

        if st.button("🔄 Actualizar estatus"):
            if actualizar_estatus_firestore(fila["firebase_id"], nuevo_estatus, observacion.upper()):
                st.success("Estatus actualizado.")
                buscar_requisicion_por_folio.clear()
                st.session_state["df_req_busqueda"] = buscar_requisicion_por_folio(folio_buscar)
                st.rerun()

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
    folio_default = f"REQ-{datetime.now().year}-{datetime.now().strftime('%m%d%H%M%S')}"
    # Catálogos automáticos tomados de las requisiciones cargadas en Firestore
    areas_catalogo = []
    proveedores_catalogo = []

    if not df_reqs.empty:
        if "area" in df_reqs.columns:
            areas_catalogo = sorted([
                str(x).strip().upper()
                for x in df_reqs["area"].dropna().unique()
                if str(x).strip()
            ])

        if "proveedor" in df_reqs.columns:
            proveedores_catalogo = sorted([
                str(x).strip().upper()
                for x in df_reqs["proveedor"].dropna().unique()
                if str(x).strip()
            ])

    col1, col2 = st.columns(2)

    with col1:
        folio = st.text_input("Folio", value=folio_default)
        fecha_req = st.date_input("Fecha", value=date.today())

        area_opcion = st.selectbox(
            "Área solicitante",
            ["-- Seleccionar área --", "-- Nueva área --"] + areas_catalogo
        )

        if area_opcion == "-- Nueva área --":
            area = st.text_input("Escribe nueva área").upper()
        elif area_opcion == "-- Seleccionar área --":
            area = ""
        else:
            area = area_opcion

        solicitante = st.text_input("Solicitante").upper()

    with col2:
        proveedor_opcion = st.selectbox(
            "Proveedor",
            ["-- Sin proveedor / Nuevo --"] + proveedores_catalogo
        )

        if proveedor_opcion == "-- Sin proveedor / Nuevo --":
            proveedor = st.text_input("Escribe proveedor").upper()
        else:
            proveedor = proveedor_opcion

        factura = st.text_input("Factura").upper()
        importe = st.number_input("Importe", min_value=0.0, step=100.0)
        cargado_a = st.text_input("Cargado a / Programa").upper()
    concepto = st.text_area("Concepto / descripción").upper()
    observaciones = st.text_area("Observaciones").upper()
    estatus = st.selectbox("Estatus", ESTATUS, index=1)
    if st.button("💾 Guardar requisición"):
        if not folio.strip():
            st.error("El folio es obligatorio.")
        elif not concepto.strip():
            st.error("El concepto es obligatorio.")
        else:
            datos = {
                "folio": folio.upper(), "fecha": str(fecha_req), "anio": str(fecha_req.year), "mes": f"{fecha_req.month:02d}",
                "concepto": concepto, "area": area, "solicitante": solicitante, "proveedor": proveedor,
                "factura": factura, "fecha_factura": "", "importe": importe, "cargado_a": cargado_a,
                "estatus": estatus, "observaciones": observaciones, "usuario": st.session_state.usuario_adq,
                "fecha_captura": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "origen": "Captura manual",
            }
            if guardar_requisicion(datos):
                st.success("Requisición guardada correctamente.")
                st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)

# ============================================================
# DOCUMENTOS Y EVIDENCIAS
# ============================================================
elif menu == "📎 Documentos y evidencias":
    if st.session_state.rol_adq == "Consulta":
        st.warning("Tu usuario solo tiene permiso de consulta.")
        st.stop()

    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.subheader("📎 Documentos y evidencias")
    st.info("Para evitar límite de lecturas, busca primero la requisición por folio.")

    folio_evidencia = st.text_input(
        "Escribe el folio de la requisición",
        placeholder="Ejemplo: 7050, REQ-2026-00001, 2025-7050"
    )

    buscar = st.button("🔎 Buscar requisición")

    if "df_req_evidencia" not in st.session_state:
        st.session_state.df_req_evidencia = pd.DataFrame()

    if buscar:
        buscar_requisicion_por_folio.clear()
        st.session_state.df_req_evidencia = buscar_requisicion_por_folio(folio_evidencia)

    df_docs_busqueda = st.session_state.df_req_evidencia

    if df_docs_busqueda.empty:
        st.warning("Busca una requisición por folio para cargar documentos.")
        st.markdown("</div>", unsafe_allow_html=True)
        st.stop()

    if len(df_docs_busqueda) > 1:
        req_id_doc = st.selectbox(
            "Selecciona requisición encontrada",
            df_docs_busqueda["id"].tolist(),
            format_func=lambda x: (
                f"{df_docs_busqueda[df_docs_busqueda['id'] == x].iloc[0]['folio']} - "
                f"{str(df_docs_busqueda[df_docs_busqueda['id'] == x].iloc[0]['concepto'])[:80]}"
            )
        )
        fila_doc = df_docs_busqueda[df_docs_busqueda["id"] == req_id_doc].iloc[0]
    else:
        fila_doc = df_docs_busqueda.iloc[0]

    st.markdown("### Datos de la requisición")
    c1, c2, c3 = st.columns(3)
    c1.write(f"**Folio:** {fila_doc.get('folio', '')}")
    c2.write(f"**Fecha:** {fila_doc.get('fecha', '')}")
    c3.write(f"**Estatus:** {fila_doc.get('estatus', '')}")

    c4, c5 = st.columns(2)
    c4.write(f"**Área:** {fila_doc.get('area', '')}")
    c5.write(f"**Proveedor:** {fila_doc.get('proveedor', '')}")

    st.write(f"**Concepto:** {fila_doc.get('concepto', '')}")

    st.markdown("---")
    st.markdown("### Subir nuevo documento")

    col_doc1, col_doc2 = st.columns(2)
    with col_doc1:
        tipo_doc = st.selectbox("Tipo de documento", TIPOS_DOCUMENTO)
    with col_doc2:
        observacion_doc = st.text_input("Observación opcional")

    archivo_doc = st.file_uploader(
        "Selecciona archivo",
        type=["pdf", "xml", "jpg", "jpeg", "png", "xlsx", "xls", "docx", "txt"],
        key="archivo_evidencia"
    )

    if st.button("📎 Guardar documento / evidencia"):
        if archivo_doc is None:
            st.error("Primero selecciona un archivo.")
        else:
            archivo_url, archivo_storage_path = subir_archivo_storage(
                archivo_doc,
                fila_doc.get("folio", ""),
                tipo_doc
            )

            datos_documento = {
                "folio": str(fila_doc.get("folio", "")),
                "anio": str(fila_doc.get("anio", "")),
                "requisicion_firebase_id": str(fila_doc.get("firebase_id", "")),
                "tipo_documento": tipo_doc,
                "nombre_archivo": archivo_doc.name,
                "archivo_url": archivo_url,
                "archivo_storage_path": archivo_storage_path,
                "observacion": observacion_doc.upper(),
                "area": str(fila_doc.get("area", "")),
                "proveedor": str(fila_doc.get("proveedor", "")),
                "concepto": str(fila_doc.get("concepto", "")),
            }

            if guardar_documento_firestore(datos_documento):
                if archivo_storage_path:
                    st.success("Documento guardado en Firebase Storage y registrado en Firestore.")
                else:
                    st.warning("Documento registrado en Firestore, pero Storage no respondió o no está configurado.")

                nuevo_estatus = None
                if tipo_doc == "Requisición firmada":
                    nuevo_estatus = "Firmada"
                elif tipo_doc in ["Evidencia de compra", "Evidencia de entrega"]:
                    nuevo_estatus = "Evidencia cargada"
                elif tipo_doc == "Firma de recibido":
                    nuevo_estatus = "Firmado recibido"

                if nuevo_estatus:
                    actualizar_estatus_firestore(
                        fila_doc["firebase_id"],
                        nuevo_estatus,
                        f"Documento cargado: {tipo_doc}"
                    )

                leer_documentos_firestore.clear()
                st.rerun()

    st.markdown("---")
    st.markdown("### Documentos cargados para esta requisición")

    docs_req = leer_documentos_firestore(str(fila_doc.get("folio", "")))

    if docs_req.empty:
        st.info("Aún no hay documentos cargados para esta requisición.")
    else:
        columnas_docs = [
            "tipo_documento", "nombre_archivo", "observacion",
            "fecha_subida", "usuario", "archivo_url", "archivo_storage_path"
        ]
        columnas_docs = [c for c in columnas_docs if c in docs_req.columns]
        st.dataframe(docs_req[columnas_docs], use_container_width=True)

        st.download_button(
            "📥 Descargar listado de documentos",
            data=crear_excel(docs_req),
            file_name=f"documentos_{fila_doc.get('folio', '')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

        for _, d in docs_req.iterrows():
            url = str(d.get("archivo_url", "")).strip()
            ruta = str(d.get("archivo_storage_path", "")).strip()
            nombre = str(d.get("nombre_archivo", "Documento"))
            tipo = str(d.get("tipo_documento", ""))

            if url:
                st.markdown(f"🔗 **{tipo}:** [{nombre}]({url})")
            elif ruta:
                st.write(f"📁 **{tipo}:** {nombre} | Ruta Storage: `{ruta}`")

    st.markdown("</div>", unsafe_allow_html=True)

# ============================================================
# IMPORTADOR HISTÓRICO
# ============================================================
elif menu == "📤 Importador histórico":
    if st.session_state.rol_adq == "Consulta":
        st.warning("Tu usuario solo tiene permiso de consulta.")
        st.stop()
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.subheader("Importador Histórico de Requisiciones")
    st.write("Carga masiva por lotes pequeños de 100 registros.")
    archivo = st.file_uploader("Sube archivo Excel", type=["xlsx", "xls"])
    if archivo:
        xls = pd.ExcelFile(archivo)
        hojas = xls.sheet_names
        st.write("Hojas detectadas:", hojas)
        hojas_seleccionadas = st.multiselect("Selecciona las hojas a importar", hojas, default=[h for h in hojas if "REQUIS" in str(h).upper()])
        if hojas_seleccionadas:
            resumen = []
            for hoja in hojas_seleccionadas:
                df_hoja, header = preparar_requisiciones(archivo, hoja)
                resumen.append({"hoja": hoja, "registros_detectados": len(df_hoja), "encabezado_fila": header + 1})
            df_resumen = pd.DataFrame(resumen)
            st.dataframe(df_resumen, use_container_width=True)
            total = int(df_resumen["registros_detectados"].sum()) if not df_resumen.empty else 0
            st.info(f"Total aproximado a importar: {total:,} requisiciones")
            if st.button("🚀 Importar a Firestore por lotes"):
                progreso = st.progress(0)
                texto = st.empty()
                total_importado = 0
                for hoja in hojas_seleccionadas:
                    df_hoja, header = preparar_requisiciones(archivo, hoja)
                    registros = df_hoja.to_dict("records")
                    for inicio in range(0, len(registros), BATCH_SIZE):
                        lote = registros[inicio:inicio + BATCH_SIZE]
                        subidos, error = subir_lote_firestore(lote)
                        total_importado += subidos
                        porcentaje = min(total_importado / total, 1.0) if total else 1.0
                        progreso.progress(porcentaje)
                        texto.write(f"Importados: {total_importado:,} de {total:,}")
                        if error:
                            st.warning(f"Lote con error: {error[:250]}")
                        time.sleep(0.3)
                leer_requisiciones_firestore.clear()
                st.success(f"✅ Importación terminada. Total importado/actualizado: {total_importado:,}")
    st.markdown("</div>", unsafe_allow_html=True)

# ============================================================
# REPORTES
# ============================================================
elif menu == "📊 Reportes":
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.subheader("Reportes de adquisiciones")
    st.warning("Reportes pausados temporalmente para evitar el error 429 de Firestore.")
    st.info("Siguiente mejora: crear reportes por año/área con consultas específicas o una colección de resumen para no leer las 10,449 requisiciones completas.")
    st.markdown("</div>", unsafe_allow_html=True)
