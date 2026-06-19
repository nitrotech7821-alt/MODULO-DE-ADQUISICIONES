import re
import time
from datetime import datetime, date
from io import BytesIO

import pandas as pd
import streamlit as st
import firebase_admin
from firebase_admin import credentials, firestore

# ============================================================
# CONFIGURACIÓN
# ============================================================
st.set_page_config(
    page_title="Sistema de Adquisiciones DIF",
    page_icon="🛒",
    layout="wide"
)

COLECCION_REQUISICIONES = "requisiciones"
BATCH_SIZE = 100

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
                return None, "No se encontró [firebase] en Secrets."
            fb = dict(st.secrets["firebase"])
            cred = credentials.Certificate(fb)
            firebase_admin.initialize_app(cred)
        return firestore.client(), None
    except Exception as e:
        return None, str(e)

db, error_firebase = conectar_firebase()

def firebase_ok():
    return db is not None

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
    ["🏠 Inicio", "📋 Requisiciones", "➕ Nueva requisición", "📤 Importador histórico", "📊 Reportes"]
)

df_reqs = leer_requisiciones_firestore()

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
        st.warning("Todavía no hay requisiciones.")
    else:
        st.subheader("Últimas requisiciones")
        cols = ["id", "folio", "fecha", "area", "concepto", "proveedor", "factura", "importe", "estatus"]
        st.dataframe(df_reqs[[c for c in cols if c in df_reqs.columns]].head(50), use_container_width=True)

# ============================================================
# REQUISICIONES
# ============================================================
elif menu == "📋 Requisiciones":
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.subheader("Consulta y seguimiento de requisiciones")
    if df_reqs.empty:
        st.warning("No hay requisiciones cargadas.")
        st.stop()
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        texto = st.text_input("Buscar folio, concepto, proveedor o factura")
    with col2:
        anio = st.selectbox("Año", ["Todos"] + sorted([x for x in df_reqs["anio"].dropna().astype(str).unique() if x], reverse=True))
    with col3:
        area_filtro = st.selectbox("Área", ["Todas"] + sorted([x for x in df_reqs["area"].dropna().unique() if x]))
    with col4:
        estatus_filtro = st.selectbox("Estatus", ["Todos"] + sorted([x for x in df_reqs["estatus"].dropna().unique() if x]))
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
    if anio != "Todos":
        df_filtrado = df_filtrado[df_filtrado["anio"].astype(str) == anio]
    if area_filtro != "Todas":
        df_filtrado = df_filtrado[df_filtrado["area"] == area_filtro]
    if estatus_filtro != "Todos":
        df_filtrado = df_filtrado[df_filtrado["estatus"] == estatus_filtro]
    st.write(f"Resultados: **{len(df_filtrado):,}**")
    cols = ["id", "folio", "fecha", "area", "concepto", "proveedor", "factura", "importe", "estatus"]
    st.dataframe(df_filtrado[[c for c in cols if c in df_filtrado.columns]], use_container_width=True)
    st.download_button("📥 Descargar resultados Excel", data=crear_excel(df_filtrado), file_name="requisiciones_filtradas.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
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
    total = len(df_reqs) + 1
    folio_default = f"REQ-{datetime.now().year}-{total:05d}"
    col1, col2 = st.columns(2)
    with col1:
        folio = st.text_input("Folio", value=folio_default)
        fecha_req = st.date_input("Fecha", value=date.today())
        area = st.text_input("Área solicitante").upper()
        solicitante = st.text_input("Solicitante").upper()
    with col2:
        proveedor = st.text_input("Proveedor").upper()
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
    if df_reqs.empty:
        st.warning("No hay información para reportar.")
        st.stop()
    col1, col2 = st.columns(2)
    with col1:
        anio_rep = st.selectbox("Año", ["Todos"] + sorted([x for x in df_reqs["anio"].dropna().astype(str).unique() if x], reverse=True))
    with col2:
        area_rep = st.selectbox("Área", ["Todas"] + sorted([x for x in df_reqs["area"].dropna().unique() if x]))
    rep = df_reqs.copy()
    if anio_rep != "Todos":
        rep = rep[rep["anio"].astype(str) == anio_rep]
    if area_rep != "Todas":
        rep = rep[rep["area"] == area_rep]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Requisiciones", len(rep))
    c2.metric("Importe total", f"${rep['importe'].sum():,.2f}")
    c3.metric("Áreas", rep["area"].nunique())
    c4.metric("Proveedores", rep["proveedor"].nunique())
    st.markdown("### Gasto por área")
    por_area = rep.groupby("area", dropna=False)["importe"].sum().reset_index().sort_values("importe", ascending=False)
    st.dataframe(por_area, use_container_width=True)
    if not por_area.empty:
        st.bar_chart(por_area.set_index("area"))
    st.markdown("### Top proveedores")
    por_proveedor = rep.groupby("proveedor", dropna=False)["importe"].sum().reset_index().sort_values("importe", ascending=False).head(20)
    st.dataframe(por_proveedor, use_container_width=True)
    st.markdown("### Gasto mensual")
    por_mes = rep.groupby("mes", dropna=False)["importe"].sum().reset_index().sort_values("mes")
    st.dataframe(por_mes, use_container_width=True)
    if not por_mes.empty:
        st.line_chart(por_mes.set_index("mes"))
    st.download_button("📥 Descargar reporte Excel", data=crear_excel(rep), file_name="reporte_adquisiciones.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    st.markdown("</div>", unsafe_allow_html=True)
