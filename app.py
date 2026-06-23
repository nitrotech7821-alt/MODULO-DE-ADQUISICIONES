import os
import re
from datetime import datetime, date
from io import BytesIO
from pathlib import Path

import pandas as pd
import pyodbc
import streamlit as st

st.set_page_config(page_title="Sistema Integral de Adquisiciones DIF", page_icon="🛒", layout="wide")

# ============================================================
# CONFIGURACIÓN SQL SERVER LOCAL
# ============================================================
SQL_SERVER = "172.16.68.101,1433"
SQL_DATABASE = "dif_adquisiciones"
SQL_USER = "sa"
SQL_PASSWORD = "sa"

CARPETA_EVIDENCIAS = r"D:\ADQUISICIONES\EVIDENCIAS"
CARPETA_RESPALDOS = r"D:\ADQUISICIONES\RESPALDOS"
BATCH_SIZE = 300

TIPOS_DOCUMENTO = [
    "Requisición firmada", "Cotización", "Factura PDF", "XML",
    "Evidencia de compra", "Evidencia de entrega", "Firma de recibido",
    "Foto", "Otro",
]

ESTATUS = [
    "Importada", "Capturada", "En firma", "Firmada", "En cotización",
    "Compra realizada", "Producto recibido", "Evidencia cargada",
    "Entregado", "Firmado recibido", "Cerrada", "Cancelada",
]

USUARIOS_INICIALES = {
    "admin": {"password": "1234", "rol": "Administrador"},
    "adquisiciones": {"password": "1234", "rol": "Adquisiciones"},
    "contabilidad": {"password": "1234", "rol": "Contabilidad"},
    "consulta": {"password": "1234", "rol": "Consulta"},
}

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
    background: rgba(255,255,255,0.94);
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

@st.cache_resource(show_spinner=False)
def conectar_sql():
    """
    Conexión compatible con tu PC.
    Tu equipo mostró que el driver instalado es: SQL Server.
    Esta función evita intentar drivers que no existen, para no provocar IM002.
    """
    drivers_instalados = pyodbc.drivers()

    candidatos = []
    for d in [
        "SQL Server",
        "SQL Server Native Client 10.0",
        "ODBC Driver 17 for SQL Server",
        "ODBC Driver 18 for SQL Server",
    ]:
        if d in drivers_instalados:
            candidatos.append(d)

    if not candidatos:
        return None, "", "No hay drivers ODBC de SQL Server instalados. Drivers detectados: " + str(drivers_instalados)

    errores = []

    servidores = [
        SQL_SERVER,
        "tcp:172.16.68.101,1433",
        r"172.16.68.101\\COMPAC",
        r"DIFHERMOSILLO\\COMPAC",
    ]

    for driver in candidatos:
        for servidor in servidores:
            try:
                if driver == "SQL Server":
                    conn_str = (
                        f"DRIVER={{{driver}}};"
                        f"SERVER={servidor};"
                        f"DATABASE={SQL_DATABASE};"
                        f"UID={SQL_USER};"
                        f"PWD={SQL_PASSWORD};"
                    )
                else:
                    conn_str = (
                        f"DRIVER={{{driver}}};"
                        f"SERVER={servidor};"
                        f"DATABASE={SQL_DATABASE};"
                        f"UID={SQL_USER};"
                        f"PWD={SQL_PASSWORD};"
                        "TrustServerCertificate=yes;"
                    )

                conn = pyodbc.connect(conn_str, timeout=8, autocommit=False)
                cur = conn.cursor()
                cur.execute("SELECT 1")
                cur.fetchone()

                return conn, f"{driver} | {servidor}", None

            except Exception as e:
                errores.append(f"Driver: {driver} | Servidor: {servidor} | Error: {e}")

    return None, "", "\n\n".join(errores)

conn, driver_usado, error_sql = conectar_sql()

def sql_ok():
    return conn is not None

def consulta_df(sql, params=None):
    if params is None:
        params = []
    return pd.read_sql(sql, conn, params=params)

def ejecutar(sql, params=None, commit=True):
    if params is None:
        params = []
    cur = conn.cursor()
    cur.execute(sql, params)
    if commit:
        conn.commit()
    return cur

def crear_excel(df):
    salida = BytesIO()
    df.to_excel(salida, index=False)
    salida.seek(0)
    return salida

def limpiar_texto(valor):
    if pd.isna(valor):
        return ""
    return str(valor).strip()

def limpiar_importe(valor):
    if pd.isna(valor) or valor == "":
        return 0.0
    texto = str(valor).replace("$", "").replace(",", "").strip()
    try:
        return float(texto)
    except Exception:
        return 0.0

def fecha_sql(valor):
    if pd.isna(valor) or valor == "":
        return None
    try:
        return pd.to_datetime(valor).date()
    except Exception:
        return None

def normalizar_archivo(texto):
    texto = str(texto).upper().strip()
    for a, b in {"Á":"A","É":"E","Í":"I","Ó":"O","Ú":"U","Ñ":"N"}.items():
        texto = texto.replace(a, b).replace(a.lower(), b)
    texto = re.sub(r"[^A-Z0-9_\- ]", "", texto)
    return texto.replace(" ", "_")[:90] or "ARCHIVO"

def encontrar_columna(df, opciones):
    columnas = {str(c).strip().upper(): c for c in df.columns}
    for opcion in opciones:
        opcion = opcion.upper()
        for col_upper, col_original in columnas.items():
            if col_upper == opcion or opcion in col_upper:
                return col_original
    return None

try:
    Path(CARPETA_EVIDENCIAS).mkdir(parents=True, exist_ok=True)
    Path(CARPETA_RESPALDOS).mkdir(parents=True, exist_ok=True)
except Exception:
    pass

def validar_usuario(usuario, password):
    if usuario in USUARIOS_INICIALES and USUARIOS_INICIALES[usuario]["password"] == password:
        return USUARIOS_INICIALES[usuario]["rol"]
    if sql_ok():
        try:
            df = consulta_df("SELECT TOP 1 rol FROM usuarios WHERE usuario=? AND password=?", [usuario, password])
            if not df.empty:
                return str(df.iloc[0]["rol"])
        except Exception:
            pass
    return ""

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
        <h1>🛒 Sistema Integral de Adquisiciones DIF</h1>
        <p>SQL Server · Requisiciones · Evidencias · Reportes</p>
    </div>
    """, unsafe_allow_html=True)
    st.markdown('<div class="card">', unsafe_allow_html=True)
    if sql_ok():
        st.success(f"SQL Server conectado | Driver: {driver_usado}")
    else:
        st.error("No se pudo conectar a SQL Server.")
        st.write("Revisa que el servidor esté encendido, que SQL Server escuche en el puerto 1433 y que el Firewall permita conexiones.")
        st.code(f"Servidor principal: {SQL_SERVER}\nBase: {SQL_DATABASE}\nUsuario: {SQL_USER}\nDrivers detectados: {pyodbc.drivers()}\n\nIntentos realizados:\n{error_sql}")
        st.stop()
    usuario = st.text_input("Usuario")
    password = st.text_input("Contraseña", type="password")
    if st.button("🔐 Entrar"):
        rol = validar_usuario(usuario, password)
        if rol:
            st.session_state.logueado_adq = True
            st.session_state.usuario_adq = usuario
            st.session_state.rol_adq = rol
            st.rerun()
        else:
            st.error("Usuario o contraseña incorrectos.")
    st.info("Usuarios iniciales: admin / adquisiciones / contabilidad / consulta. Contraseña: 1234")
    st.markdown("</div>", unsafe_allow_html=True)
    return False

if not login():
    st.stop()

@st.cache_data(ttl=60, show_spinner=False)
def leer_areas():
    try:
        return consulta_df("SELECT id, nombre FROM areas ORDER BY nombre")
    except Exception:
        return pd.DataFrame(columns=["id", "nombre"])

@st.cache_data(ttl=60, show_spinner=False)
def leer_proveedores():
    try:
        return consulta_df("SELECT id, nombre, telefono, correo FROM proveedores ORDER BY nombre")
    except Exception:
        return pd.DataFrame(columns=["id", "nombre", "telefono", "correo"])

def guardar_area(nombre):
    nombre = str(nombre).upper().strip()
    if not nombre:
        return False
    ejecutar("IF NOT EXISTS (SELECT 1 FROM areas WHERE nombre=?) INSERT INTO areas(nombre) VALUES (?)", [nombre, nombre])
    leer_areas.clear()
    return True

def guardar_proveedor(nombre, telefono="", correo=""):
    nombre = str(nombre).upper().strip()
    if not nombre:
        return False
    ejecutar("IF NOT EXISTS (SELECT 1 FROM proveedores WHERE nombre=?) INSERT INTO proveedores(nombre, telefono, correo) VALUES (?, ?, ?)", [nombre, nombre, telefono, correo])
    leer_proveedores.clear()
    return True

def registrar_movimiento(requisicion_id, accion):
    try:
        ejecutar("INSERT INTO movimientos(requisicion_id, accion, usuario) VALUES (?, ?, ?)", [requisicion_id, accion, st.session_state.usuario_adq])
    except Exception:
        pass

def obtener_siguiente_folio():
    return f"REQ-{datetime.now().year}-{datetime.now().strftime('%m%d%H%M%S')}"

def insertar_requisicion(datos):
    sql = """
    INSERT INTO requisiciones
    (folio, fecha, area, solicitante, proveedor, factura, importe, concepto, observaciones, programa, estatus)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    ejecutar(sql, [
        datos.get("folio"), datos.get("fecha"), datos.get("area"), datos.get("solicitante"),
        datos.get("proveedor"), datos.get("factura"), datos.get("importe"), datos.get("concepto"),
        datos.get("observaciones"), datos.get("programa"), datos.get("estatus"),
    ])
    df = consulta_df("SELECT MAX(id) AS id FROM requisiciones")
    req_id = int(df.iloc[0]["id"])
    registrar_movimiento(req_id, "Requisición capturada/importada")
    return req_id

def actualizar_estatus(req_id, estatus, observacion):
    nota = f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M')}] {observacion}"
    ejecutar("UPDATE requisiciones SET estatus=?, observaciones=ISNULL(observaciones,'') + ? WHERE id=?", [estatus, nota, req_id])
    registrar_movimiento(req_id, f"Estatus actualizado a {estatus}")

def buscar_requisiciones(folio="", texto="", anio="", area="", proveedor="", limite=500):
    filtros, params = [], []
    if folio:
        filtros.append("folio LIKE ?")
        params.append(f"%{folio}%")
    if texto:
        filtros.append("(concepto LIKE ? OR factura LIKE ? OR solicitante LIKE ?)")
        params.extend([f"%{texto}%", f"%{texto}%", f"%{texto}%"])
    if anio and anio != "Todos":
        filtros.append("YEAR(fecha)=?")
        params.append(int(anio))
    if area and area != "Todas":
        filtros.append("area=?")
        params.append(area)
    if proveedor and proveedor != "Todos":
        filtros.append("proveedor=?")
        params.append(proveedor)
    where = "WHERE " + " AND ".join(filtros) if filtros else ""
    sql = f"""
    SELECT TOP {int(limite)} id, folio, fecha, area, solicitante, proveedor, factura,
           importe, concepto, observaciones, programa, estatus, fecha_creacion
    FROM requisiciones
    {where}
    ORDER BY id DESC
    """
    return consulta_df(sql, params)

def leer_requisicion_id(req_id):
    return consulta_df("SELECT * FROM requisiciones WHERE id=?", [req_id])

def guardar_evidencia(req_id, archivo, tipo_documento, observacion):
    if archivo is None:
        st.error("Selecciona un archivo.")
        return False
    req_df = leer_requisicion_id(req_id)
    if req_df.empty:
        st.error("No se encontró la requisición.")
        return False
    folio = str(req_df.iloc[0]["folio"])
    carpeta_req = Path(CARPETA_EVIDENCIAS) / normalizar_archivo(folio)
    carpeta_req.mkdir(parents=True, exist_ok=True)
    extension = Path(archivo.name).suffix.lower()
    nombre_base = normalizar_archivo(Path(archivo.name).stem)
    fecha = datetime.now().strftime("%Y%m%d_%H%M%S")
    nombre_final = f"{fecha}_{normalizar_archivo(tipo_documento)}_{nombre_base}{extension}"
    ruta_final = carpeta_req / nombre_final
    with open(ruta_final, "wb") as f:
        f.write(archivo.getbuffer())
    ejecutar("INSERT INTO evidencias(requisicion_id, nombre_archivo, ruta_archivo, tipo_archivo) VALUES (?, ?, ?, ?)", [req_id, archivo.name, str(ruta_final), tipo_documento])
    registrar_movimiento(req_id, f"Evidencia cargada: {tipo_documento} {observacion}")
    return True

def leer_evidencias(req_id):
    return consulta_df("SELECT id, nombre_archivo, ruta_archivo, tipo_archivo, fecha_subida FROM evidencias WHERE requisicion_id=? ORDER BY id DESC", [req_id])

def leer_movimientos(req_id):
    return consulta_df("SELECT accion, usuario, fecha FROM movimientos WHERE requisicion_id=? ORDER BY id DESC", [req_id])

def leer_hoja_inteligente(archivo, hoja):
    mejor_df, mejor_header, mejor_score = None, 0, -1
    for header in range(0, 8):
        try:
            archivo.seek(0)
            df_tmp = pd.read_excel(archivo, sheet_name=hoja, header=header)
            columnas = " ".join([str(c).upper() for c in df_tmp.columns])
            score = sum(1 for p in ["FECHA", "REQUI", "CONCEPTO", "AREA", "PROVEEDOR", "IMPORTE"] if p in columnas)
            if score > mejor_score:
                mejor_df, mejor_header, mejor_score = df_tmp, header, score
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
    col_importe = encontrar_columna(df, ["IMPORTE", "TOTAL", "MONTO"])
    col_programa = encontrar_columna(df, ["CARGADO A", "CARGADO", "PROGRAMA"])
    registros = []
    for _, row in df.iterrows():
        folio = limpiar_texto(row[col_folio]) if col_folio else ""
        concepto = limpiar_texto(row[col_concepto]).upper() if col_concepto else ""
        if not folio and not concepto:
            continue
        registros.append({
            "folio": str(folio).replace(".0", "") if folio else "",
            "fecha": fecha_sql(row[col_fecha]) if col_fecha else None,
            "area": limpiar_texto(row[col_area]).upper() if col_area else "",
            "solicitante": "",
            "proveedor": limpiar_texto(row[col_proveedor]).upper() if col_proveedor else "",
            "factura": limpiar_texto(row[col_factura]).upper() if col_factura else "",
            "importe": limpiar_importe(row[col_importe]) if col_importe else 0.0,
            "concepto": concepto,
            "observaciones": "",
            "programa": limpiar_texto(row[col_programa]).upper() if col_programa else "",
            "estatus": "Importada",
        })
    return pd.DataFrame(registros), header

def importar_lote_sql(df_importar):
    insertados, errores = 0, []
    for _, r in df_importar.iterrows():
        try:
            folio = str(r.get("folio", "")).strip()
            fecha = r.get("fecha", None)
            existe = consulta_df("SELECT TOP 1 id FROM requisiciones WHERE folio=? AND ISNULL(CONVERT(VARCHAR(10), fecha, 120),'')=ISNULL(CONVERT(VARCHAR(10), ?, 120),'')", [folio, fecha])
            if not existe.empty:
                continue
            req_id = insertar_requisicion({
                "folio": folio, "fecha": fecha, "area": r.get("area", ""), "solicitante": r.get("solicitante", ""),
                "proveedor": r.get("proveedor", ""), "factura": r.get("factura", ""), "importe": float(r.get("importe", 0) or 0),
                "concepto": r.get("concepto", ""), "observaciones": r.get("observaciones", ""),
                "programa": r.get("programa", ""), "estatus": r.get("estatus", "Importada"),
            })
            insertados += 1
            if r.get("area", ""):
                guardar_area(r.get("area", ""))
            if r.get("proveedor", ""):
                guardar_proveedor(r.get("proveedor", ""))
        except Exception as e:
            errores.append(str(e)[:200])
    return insertados, errores

st.markdown("""
<div class="header-card">
    <h1>🛒 Sistema Integral DIF Hermosillo</h1>
    <p>Adquisiciones · Contabilidad · Evidencias · Reportes · SQL Server Local</p>
</div>
""", unsafe_allow_html=True)

st.sidebar.success(f"Usuario: {st.session_state.usuario_adq} | Rol: {st.session_state.rol_adq}")
st.sidebar.success("SQL Server conectado" if sql_ok() else "SQL Server no conectado")
st.sidebar.caption(f"Driver: {driver_usado}")
if st.sidebar.button("🔄 Actualizar pantalla"):
    leer_areas.clear(); leer_proveedores.clear(); st.rerun()
if st.sidebar.button("Cerrar sesión"):
    st.session_state.logueado_adq = False; st.session_state.usuario_adq = ""; st.session_state.rol_adq = ""; st.rerun()

menu = st.sidebar.radio("Menú", ["🏠 Inicio", "📋 Requisiciones", "➕ Nueva requisición", "📎 Documentos y evidencias", "📤 Importar Excel", "🏢 Áreas", "🏭 Proveedores", "📊 Reportes"])

if menu == "🏠 Inicio":
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.subheader("Dashboard general")
    try:
        total_req = int(consulta_df("SELECT COUNT(*) total FROM requisiciones").iloc[0]["total"])
        total_evid = int(consulta_df("SELECT COUNT(*) total FROM evidencias").iloc[0]["total"])
        total_area = int(consulta_df("SELECT COUNT(*) total FROM areas").iloc[0]["total"])
        total_prov = int(consulta_df("SELECT COUNT(*) total FROM proveedores").iloc[0]["total"])
        total_importe = float(consulta_df("SELECT ISNULL(SUM(importe),0) total FROM requisiciones").iloc[0]["total"])
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Requisiciones", f"{total_req:,}"); c2.metric("Evidencias", f"{total_evid:,}"); c3.metric("Áreas", f"{total_area:,}"); c4.metric("Proveedores", f"{total_prov:,}"); c5.metric("Importe total", f"${total_importe:,.2f}")
        st.markdown("### Últimas requisiciones")
        st.dataframe(buscar_requisiciones(limite=50), use_container_width=True)
    except Exception as e:
        st.error(f"No se pudo cargar dashboard: {e}")
    st.markdown("</div>", unsafe_allow_html=True)

elif menu == "📋 Requisiciones":
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.subheader("Consulta y seguimiento de requisiciones")
    areas, proveedores = leer_areas(), leer_proveedores()
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        folio = st.text_input("Folio"); texto = st.text_input("Texto / factura / concepto").upper()
    with c2:
        anio = st.selectbox("Año", ["Todos", "2026", "2025", "2024", "2023"])
    with c3:
        area = st.selectbox("Área", ["Todas"] + (areas["nombre"].astype(str).tolist() if not areas.empty else []))
    with c4:
        proveedor = st.selectbox("Proveedor", ["Todos"] + (proveedores["nombre"].astype(str).tolist() if not proveedores.empty else []))
        limite = st.number_input("Límite", min_value=50, max_value=5000, value=500, step=50)
    if st.button("🔎 Buscar"):
        st.session_state["df_busqueda_sql"] = buscar_requisiciones(folio, texto, anio, area, proveedor, limite)
    df = st.session_state.get("df_busqueda_sql", pd.DataFrame())
    if df.empty:
        st.info("Usa los filtros y presiona Buscar.")
    else:
        st.success(f"Resultados: {len(df):,}"); st.dataframe(df, use_container_width=True)
        st.download_button("📥 Descargar Excel", data=crear_excel(df), file_name="requisiciones_sql.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        if st.session_state.rol_adq != "Consulta":
            st.markdown("### Actualizar estatus")
            req_id = st.selectbox("ID requisición", df["id"].tolist())
            nuevo_estatus = st.selectbox("Nuevo estatus", ESTATUS)
            observacion = st.text_area("Observación")
            if st.button("🔄 Guardar estatus"):
                actualizar_estatus(int(req_id), nuevo_estatus, observacion.upper()); st.success("Estatus actualizado."); st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)

elif menu == "➕ Nueva requisición":
    if st.session_state.rol_adq == "Consulta": st.warning("Tu usuario solo tiene permiso de consulta."); st.stop()
    st.markdown('<div class="card">', unsafe_allow_html=True); st.subheader("Nueva requisición")
    areas, proveedores = leer_areas(), leer_proveedores()
    c1, c2 = st.columns(2)
    with c1:
        folio = st.text_input("Folio", value=obtener_siguiente_folio())
        fecha_req = st.date_input("Fecha", value=date.today())
        area_opcion = st.selectbox("Área solicitante", ["-- Seleccionar área --", "-- Nueva área --"] + (areas["nombre"].astype(str).tolist() if not areas.empty else []))
        area = st.text_input("Escribe nueva área").upper() if area_opcion == "-- Nueva área --" else ("" if area_opcion == "-- Seleccionar área --" else area_opcion)
        solicitante = st.text_input("Solicitante").upper()
    with c2:
        proveedor_opcion = st.selectbox("Proveedor", ["-- Sin proveedor / Nuevo --"] + (proveedores["nombre"].astype(str).tolist() if not proveedores.empty else []))
        proveedor = st.text_input("Escribe proveedor").upper() if proveedor_opcion == "-- Sin proveedor / Nuevo --" else proveedor_opcion
        factura = st.text_input("Factura").upper(); importe = st.number_input("Importe", min_value=0.0, step=100.0); programa = st.text_input("Programa / cargado a").upper()
    concepto = st.text_area("Concepto / descripción").upper(); observaciones = st.text_area("Observaciones").upper(); estatus = st.selectbox("Estatus", ESTATUS, index=1)
    if st.button("💾 Guardar requisición"):
        if not folio.strip(): st.error("El folio es obligatorio.")
        elif not concepto.strip(): st.error("El concepto es obligatorio.")
        else:
            if area: guardar_area(area)
            if proveedor: guardar_proveedor(proveedor)
            req_id = insertar_requisicion({"folio": folio.upper(), "fecha": fecha_req, "area": area, "solicitante": solicitante, "proveedor": proveedor, "factura": factura, "importe": importe, "concepto": concepto, "observaciones": observaciones, "programa": programa, "estatus": estatus})
            st.success(f"Requisición guardada correctamente. ID: {req_id}"); st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)

elif menu == "📎 Documentos y evidencias":
    if st.session_state.rol_adq == "Consulta": st.warning("Tu usuario solo tiene permiso de consulta."); st.stop()
    st.markdown('<div class="card">', unsafe_allow_html=True); st.subheader("Documentos y evidencias")
    folio = st.text_input("Buscar por folio")
    if st.button("🔎 Buscar requisición"):
        st.session_state["df_evidencia_req"] = buscar_requisiciones(folio=folio, limite=20)
    df_req = st.session_state.get("df_evidencia_req", pd.DataFrame())
    if df_req.empty:
        st.info("Busca una requisición para subir documentos."); st.stop()
    req_id = st.selectbox("Selecciona requisición", df_req["id"].tolist(), format_func=lambda x: f"{x} - {df_req[df_req['id']==x].iloc[0]['folio']} - {str(df_req[df_req['id']==x].iloc[0]['concepto'])[:70]}")
    fila = df_req[df_req["id"] == req_id].iloc[0]
    st.write(f"**Folio:** {fila['folio']}  |  **Área:** {fila['area']}  |  **Proveedor:** {fila['proveedor']}"); st.write(f"**Concepto:** {fila['concepto']}")
    tipo = st.selectbox("Tipo de documento", TIPOS_DOCUMENTO); obs = st.text_input("Observación opcional"); archivo = st.file_uploader("Archivo", type=["pdf","xml","jpg","jpeg","png","xlsx","xls","docx","txt"])
    if st.button("📎 Guardar evidencia"):
        if guardar_evidencia(int(req_id), archivo, tipo, obs.upper()): st.success("Evidencia guardada en el servidor."); st.rerun()
    st.markdown("### Evidencias guardadas")
    evid = leer_evidencias(int(req_id))
    if evid.empty: st.info("No hay evidencias para esta requisición.")
    else:
        st.dataframe(evid, use_container_width=True)
        for _, r in evid.iterrows():
            ruta = str(r["ruta_archivo"]); nombre = str(r["nombre_archivo"])
            if os.path.exists(ruta):
                with open(ruta, "rb") as f: st.download_button(f"📥 Descargar {nombre}", data=f.read(), file_name=nombre, key=f"down_{r['id']}")
            else: st.warning(f"No se encontró el archivo en disco: {ruta}")
    st.markdown("### Bitácora"); st.dataframe(leer_movimientos(int(req_id)), use_container_width=True)
    st.markdown("</div>", unsafe_allow_html=True)

elif menu == "📤 Importar Excel":
    if st.session_state.rol_adq == "Consulta": st.warning("Tu usuario solo tiene permiso de consulta."); st.stop()
    st.markdown('<div class="card">', unsafe_allow_html=True); st.subheader("Importar histórico desde Excel a SQL Server")
    archivo = st.file_uploader("Sube archivo Excel", type=["xlsx", "xls"])
    if archivo:
        xls = pd.ExcelFile(archivo); hojas = xls.sheet_names; st.write("Hojas detectadas:", hojas)
        hojas_seleccionadas = st.multiselect("Selecciona hojas", hojas, default=[h for h in hojas if "REQUIS" in str(h).upper()])
        if hojas_seleccionadas:
            resumen, datos_por_hoja = [], {}
            for hoja in hojas_seleccionadas:
                df_hoja, header = preparar_requisiciones(archivo, hoja); datos_por_hoja[hoja] = df_hoja; resumen.append({"hoja": hoja, "registros_detectados": len(df_hoja), "encabezado_fila": header + 1})
            df_resumen = pd.DataFrame(resumen); st.dataframe(df_resumen, use_container_width=True)
            total = int(df_resumen["registros_detectados"].sum()) if not df_resumen.empty else 0; st.info(f"Total aproximado a importar: {total:,} requisiciones")
            if st.button("🚀 Importar a SQL Server"):
                progreso = st.progress(0); texto = st.empty(); total_insertados = 0; procesados = 0
                for hoja, df_hoja in datos_por_hoja.items():
                    for inicio in range(0, len(df_hoja), BATCH_SIZE):
                        lote = df_hoja.iloc[inicio:inicio+BATCH_SIZE]; insertados, errores = importar_lote_sql(lote); total_insertados += insertados; procesados += len(lote)
                        progreso.progress(min(procesados / total, 1.0) if total else 1.0); texto.write(f"Procesados: {procesados:,} de {total:,} | Insertados nuevos: {total_insertados:,}")
                        if errores: st.warning(f"Errores en lote: {errores[:3]}")
                st.success(f"Importación terminada. Insertados nuevos: {total_insertados:,}")
    st.markdown("</div>", unsafe_allow_html=True)

elif menu == "🏢 Áreas":
    st.markdown('<div class="card">', unsafe_allow_html=True); st.subheader("Catálogo de áreas")
    nueva = st.text_input("Nueva área").upper()
    if st.button("💾 Guardar área"):
        if guardar_area(nueva): st.success("Área guardada."); st.rerun()
    st.dataframe(leer_areas(), use_container_width=True); st.markdown("</div>", unsafe_allow_html=True)

elif menu == "🏭 Proveedores":
    st.markdown('<div class="card">', unsafe_allow_html=True); st.subheader("Catálogo de proveedores")
    c1, c2, c3 = st.columns(3)
    with c1: nombre = st.text_input("Proveedor").upper()
    with c2: telefono = st.text_input("Teléfono")
    with c3: correo = st.text_input("Correo")
    if st.button("💾 Guardar proveedor"):
        if guardar_proveedor(nombre, telefono, correo): st.success("Proveedor guardado."); st.rerun()
    st.dataframe(leer_proveedores(), use_container_width=True); st.markdown("</div>", unsafe_allow_html=True)

elif menu == "📊 Reportes":
    st.markdown('<div class="card">', unsafe_allow_html=True); st.subheader("Reportes")
    areas, proveedores = leer_areas(), leer_proveedores()
    c1, c2, c3 = st.columns(3)
    with c1: anio = st.selectbox("Año", ["Todos", "2026", "2025", "2024", "2023"])
    with c2: area = st.selectbox("Área", ["Todas"] + (areas["nombre"].astype(str).tolist() if not areas.empty else []))
    with c3: proveedor = st.selectbox("Proveedor", ["Todos"] + (proveedores["nombre"].astype(str).tolist() if not proveedores.empty else []))
    limite = st.slider("Máximo de registros", 100, 10000, 3000, step=100)
    if st.button("📊 Generar reporte"):
        st.session_state["df_reporte_sql"] = buscar_requisiciones(anio=anio, area=area, proveedor=proveedor, limite=limite)
    rep = st.session_state.get("df_reporte_sql", pd.DataFrame())
    if rep.empty: st.info("Selecciona filtros y genera el reporte."); st.stop()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Requisiciones", len(rep)); c2.metric("Importe total", f"${rep['importe'].sum():,.2f}"); c3.metric("Áreas", rep["area"].nunique()); c4.metric("Proveedores", rep["proveedor"].nunique())
    st.dataframe(rep, use_container_width=True)
    st.download_button("📥 Descargar reporte Excel", data=crear_excel(rep), file_name="reporte_adquisiciones_sql.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    st.markdown("### Gasto por área"); por_area = rep.groupby("area", dropna=False)["importe"].sum().reset_index().sort_values("importe", ascending=False); st.dataframe(por_area, use_container_width=True)
    if not por_area.empty: st.bar_chart(por_area.set_index("area"))
    st.markdown("### Top proveedores"); por_prov = rep.groupby("proveedor", dropna=False)["importe"].sum().reset_index().sort_values("importe", ascending=False).head(20); st.dataframe(por_prov, use_container_width=True)
    st.markdown("</div>", unsafe_allow_html=True)
