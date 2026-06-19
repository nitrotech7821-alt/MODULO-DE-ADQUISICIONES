
import os
import re
import time
from datetime import datetime
from io import BytesIO

import pandas as pd
import streamlit as st
import firebase_admin
from firebase_admin import credentials, firestore

# ============================================================
# CONFIGURACIÓN
# ============================================================
st.set_page_config(
    page_title="Importador Histórico de Requisiciones",
    page_icon="📤",
    layout="wide"
)

COLECCION_REQUISICIONES = "requisiciones"
BATCH_SIZE = 100

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
# FUNCIONES
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

    texto = str(valor)
    texto = texto.replace("$", "").replace(",", "").strip()

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


def leer_hoja_inteligente(archivo, hoja):
    mejor_df = None
    mejor_header = 0
    mejor_score = -1

    for header in range(0, 8):
        try:
            archivo.seek(0)
            df_tmp = pd.read_excel(archivo, sheet_name=hoja, header=header)

            columnas = " ".join([str(c).upper() for c in df_tmp.columns])
            score = sum(
                1 for palabra in ["FECHA", "REQUI", "CONCEPTO", "AREA", "PROVEEDOR", "IMPORTE"]
                if palabra in columnas
            )

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

        registro = {
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
        }

        registros.append(registro)

    return pd.DataFrame(registros), header


def doc_id_requisicion(registro):
    folio = limpiar_folio(registro.get("folio", ""))
    anio = str(registro.get("anio", "")).strip()

    if anio:
        return f"{anio}-{folio}"

    return folio


def subir_lote_firestore(registros, reintentos=3):
    """
    Sube un lote pequeño a Firestore.
    Si Firestore tarda o falla por red, reintenta varias veces.
    """
    ultimo_error = None

    for intento in range(1, reintentos + 1):
        try:
            batch = db.batch()
            contador = 0

            for registro in registros:
                doc_id = doc_id_requisicion(registro)

                if not doc_id:
                    continue

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


def descargar_excel(df):
    salida = BytesIO()
    df.to_excel(salida, index=False)
    salida.seek(0)
    return salida


# ============================================================
# INTERFAZ
# ============================================================
st.title("📤 Importador Histórico de Requisiciones a Firestore")
st.write("Carga masiva por lotes pequeños de 100 registros para evitar errores de Firestore.")

if firebase_ok():
    st.success("✅ Firebase conectado")
else:
    st.error("❌ Firebase no conectado")
    st.write(error_firebase)
    st.stop()

archivo = st.file_uploader("Sube el archivo REQUISICIONES 2025.xlsx", type=["xlsx", "xls"])

if archivo:
    xls = pd.ExcelFile(archivo)
    hojas = xls.sheet_names

    st.write("Hojas detectadas:", hojas)

    hojas_seleccionadas = st.multiselect(
        "Selecciona las hojas a importar",
        hojas,
        default=[h for h in hojas if "REQUIS" in str(h).upper()]
    )

    if hojas_seleccionadas:
        resumen = []

        for hoja in hojas_seleccionadas:
            df_hoja, header = preparar_requisiciones(archivo, hoja)
            resumen.append({
                "hoja": hoja,
                "registros_detectados": len(df_hoja),
                "encabezado_fila": header + 1
            })

        st.subheader("Resumen antes de importar")
        df_resumen = pd.DataFrame(resumen)
        st.dataframe(df_resumen, use_container_width=True)

        total = int(df_resumen["registros_detectados"].sum()) if not df_resumen.empty else 0
        st.info(f"Total aproximado a importar: {total:,} requisiciones")

        if st.button("🚀 Importar a Firestore por lotes"):
            progreso = st.progress(0)
            texto = st.empty()

            total_importado = 0
            total_general = total

            for hoja in hojas_seleccionadas:
                df_hoja, header = preparar_requisiciones(archivo, hoja)

                if df_hoja.empty:
                    continue

                registros = df_hoja.to_dict("records")

                for inicio in range(0, len(registros), BATCH_SIZE):
                    fin = inicio + BATCH_SIZE
                    lote = registros[inicio:fin]

                    subidos, error = subir_lote_firestore(lote)
                    total_importado += subidos

                    porcentaje = min(total_importado / total_general, 1.0) if total_general else 1.0
                    progreso.progress(porcentaje)

                    if error:
                        st.warning(f"Lote con error. Se continuará con el siguiente. Error: {error[:250]}")
                    texto.write(f"Importados: {total_importado:,} de {total_general:,}")

                    time.sleep(0.5)

            st.success(f"✅ Importación terminada. Total importado/actualizado: {total_importado:,}")

        st.download_button(
            "📥 Descargar resumen detectado",
            data=descargar_excel(pd.DataFrame(resumen)),
            file_name="resumen_importacion_requisiciones.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
