"""
Agenda — widget de escritorio para Google Calendar (Windows 11).

Ventana flotante, semitransparente, que queda por debajo de las aplicaciones
y muestra los próximos eventos de los calendarios elegidos.
Lee cada calendario por su "Dirección secreta en formato iCal" (solo lectura).

Uso:
  · Arrastrar con el mouse para mover (la posición se recuerda).
  · Clic derecho: actualizar, calendarios, días, opacidad, ayuda, salir.
  · Ctrl + Alt + A trae el widget al frente; repetirlo lo devuelve al fondo.
  · La configuración se guarda en %APPDATA%\\Agenda\\config.json
"""
import ctypes
import ctypes.wintypes
import datetime as dt
import hashlib
import json
import os
import queue
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
import tkinter as tk
from tkinter import messagebox

import icalendar
import recurring_ical_events
import requests

APP = "Agenda"
VERSION = "1.4"
ATAJO_TEXTO = "Ctrl + Alt + A"


def zona_local():
    """Zona horaria configurada en Windows (con horario de verano incluido)."""
    try:
        from tzlocal import get_localzone
        return get_localzone()
    except Exception:
        return dt.datetime.now().astimezone().tzinfo


TZ = zona_local()
DIR_CFG = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), APP)
ARCH_CFG = os.path.join(DIR_CFG, "config.json")
# copia local de cada calendario, para mostrar algo cuando no hay conexión
DIR_CACHE = os.path.join(os.environ.get("LOCALAPPDATA") or DIR_CFG, APP, "cache")
REINTENTO_MIN = 2   # si algo falló, reintentar antes que el refresco normal

CFG_BASE = {
    "calendarios": [],      # [{"nombre", "url", "color", "visible"}]
    "x": 60,
    "y": 60,
    "dias": 7,
    "refresco_min": 10,
    "opacidad": 0.85,
    "alto_max": 520,        # alto máximo de la lista de eventos, en píxeles
}

BG = "#111111"
FG = "#F2F2F2"
DIM = "#7A7A7A"
ACENTO = "#FFCA01"
CAMPO = "#1C1C1C"
VERDE = "#81C784"
ROJO = "#E57373"
AMBAR = "#E0B050"
FUENTE = "Segoe UI"
ANCHO = 320
MAX_EVENTOS = 120
ALTO_MIN = 120
ALTO_BASE = 520
BARRA = "#5A5A5A"
BARRA_ACTIVA = "#9A9A9A"
PALETA = ["#FFCA01", "#4FC3F7", "#81C784", "#F48FB1", "#CE93D8", "#FFAB91", "#B0BEC5", "#FFF59D"]

# factor de escala de Windows (1.0 = 100 %, 1.25 = 125 %…); se calcula al abrir
E = 1.0


def px(n):
    """Convierte píxeles de diseño (a 100 %) en píxeles reales de la pantalla."""
    return int(round(n * E))


DIAS_ES = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
MESES_ES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


# ───────────────────────── configuración ─────────────────────────
def cargar_cfg():
    cfg = json.loads(json.dumps(CFG_BASE))
    try:
        with open(ARCH_CFG, encoding="utf-8") as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    return cfg


def guardar_cfg(cfg):
    try:
        os.makedirs(DIR_CFG, exist_ok=True)
        tmp = ARCH_CFG + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, ARCH_CFG)
    except OSError:
        pass


# ───────────────────────── calendario ─────────────────────────
def bajar(url):
    """Descarga el contenido iCal de una dirección (bytes)."""
    url = url.strip()
    if url.lower().startswith("webcal://"):
        url = "https://" + url[9:]
    r = requests.get(url, timeout=(6, 20))
    r.raise_for_status()
    return r.content


def descargar(url):
    return icalendar.Calendar.from_ical(bajar(url))


def ruta_cache(url):
    clave = hashlib.sha256(url.strip().encode("utf-8")).hexdigest()[:24]
    return os.path.join(DIR_CACHE, clave + ".ics")


def guardar_cache(url, contenido):
    try:
        os.makedirs(DIR_CACHE, exist_ok=True)
        ruta = ruta_cache(url)
        with open(ruta + ".tmp", "wb") as f:
            f.write(contenido)
        os.replace(ruta + ".tmp", ruta)
    except OSError:
        pass


def leer_cache(url):
    """Devuelve (contenido, momento de la última descarga correcta) o None."""
    ruta = ruta_cache(url)
    try:
        with open(ruta, "rb") as f:
            contenido = f.read()
        return contenido, dt.datetime.fromtimestamp(os.path.getmtime(ruta), TZ)
    except OSError:
        return None


def borrar_cache(url):
    try:
        os.remove(ruta_cache(url))
    except OSError:
        pass


def nombre_del_calendario(cal):
    return str(cal.get("X-WR-CALNAME", "")).strip()


def a_fecha_hora(valor):
    """Devuelve (datetime con zona horaria local, es_todo_el_dia)."""
    if not isinstance(valor, dt.datetime):
        return dt.datetime.combine(valor, dt.time.min, TZ), True
    if valor.tzinfo is None:
        return valor.replace(tzinfo=TZ), False
    return valor.astimezone(TZ), False


def eventos_de(cal, color, desde, hasta):
    salida = []
    for ev in recurring_ical_events.of(cal).between(desde, hasta):
        if str(ev.get("STATUS", "")).upper() == "CANCELLED":
            continue
        inicio, todo_dia = a_fecha_hora(ev["DTSTART"].dt)
        fin = a_fecha_hora(ev["DTEND"].dt)[0] if ev.get("DTEND") else inicio
        titulo = str(ev.get("SUMMARY", "")).strip() or "(sin título)"

        if todo_dia:
            # un renglón por cada día que abarca dentro del rango
            dia = max(inicio, desde)
            ultimo = max(fin, inicio + dt.timedelta(days=1))
            while dia < ultimo and dia < hasta:
                salida.append({"dia": dia.date(), "inicio": dia, "fin": fin,
                               "todo_dia": True, "titulo": titulo, "color": color})
                dia += dt.timedelta(days=1)
        else:
            dia = inicio.date() if inicio >= desde else desde.date()
            salida.append({"dia": dia, "inicio": inicio, "fin": fin,
                           "todo_dia": False, "titulo": titulo, "color": color})
    return salida


def leer_calendario(c, desde, hasta):
    """Lee un calendario. Si no hay conexión, usa la última copia guardada.

    Devuelve (eventos, estado) con estado = {"nombre", "estado", "momento"}:
      "ok"        descargado ahora
      "guardado"  sin conexión: eventos de la copia guardada en "momento"
      "sin_datos" sin conexión y sin copia guardada
    """
    nombre = c.get("nombre", "calendario")
    color = c.get("color", ACENTO)
    try:
        contenido = bajar(c["url"])
        eventos = eventos_de(icalendar.Calendar.from_ical(contenido), color, desde, hasta)
        guardar_cache(c["url"], contenido)
        return eventos, {"nombre": nombre, "estado": "ok", "momento": dt.datetime.now(TZ)}
    except Exception:
        pass
    copia = leer_cache(c["url"])
    if copia:
        try:
            eventos = eventos_de(icalendar.Calendar.from_ical(copia[0]), color, desde, hasta)
            return eventos, {"nombre": nombre, "estado": "guardado", "momento": copia[1]}
        except Exception:
            pass
    return [], {"nombre": nombre, "estado": "sin_datos", "momento": None}


def leer_eventos(calendarios, dias):
    """Lee los calendarios visibles en paralelo. Devuelve (eventos, estados)."""
    desde = dt.datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    hasta = desde + dt.timedelta(days=dias)
    visibles = [c for c in calendarios if c.get("visible", True)]
    eventos, estados = [], []
    if visibles:
        with ThreadPoolExecutor(max_workers=min(6, len(visibles))) as grupo:
            for evs, estado in grupo.map(lambda c: leer_calendario(c, desde, hasta), visibles):
                eventos += evs
                estados.append(estado)
    eventos.sort(key=lambda e: (e["dia"], not e["todo_dia"], e["inicio"], e["titulo"]))
    return eventos, estados


def cuando(momento):
    """«14:20», «ayer 18:30» o «3 nov 18:30»."""
    hoy = dt.datetime.now(TZ).date()
    hora = momento.strftime("%H:%M")
    if momento.date() == hoy:
        return hora
    if momento.date() == hoy - dt.timedelta(days=1):
        return "ayer " + hora
    return f"{momento.day} {MESES_ES[momento.month - 1]} {hora}"


def resumen_estado(estados):
    """Texto y color de la cabecera, y avisos para el cuerpo, según cómo salió la lectura."""
    fallidos = [e for e in estados if e["estado"] != "ok"]
    guardados = [e for e in fallidos if e["estado"] == "guardado"]
    sin_datos = [e for e in fallidos if e["estado"] == "sin_datos"]
    ahora = dt.datetime.now(TZ).strftime("%H:%M")
    avisos = []
    if not fallidos:
        return f"Actualizado {ahora}", DIM, avisos
    if len(fallidos) < len(estados):
        cabecera, color = f"Actualización parcial {ahora}", AMBAR
        for e in guardados:
            avisos.append(f"«{e['nombre']}» sin conexión: se muestran sus eventos "
                          f"guardados de {cuando(e['momento'])}.")
    else:
        color = ROJO
        if guardados:
            mas_viejo = min(e["momento"] for e in guardados)
            cabecera = f"Sin conexión · última correcta {cuando(mas_viejo)}"
            avisos.append(f"Sin conexión. Se muestran los eventos guardados de "
                          f"{cuando(mas_viejo)}; pueden estar desactualizados.")
        else:
            cabecera = "Sin conexión"
    for e in sin_datos:
        avisos.append(f"«{e['nombre']}» sin conexión y sin eventos guardados.")
    return cabecera, color, avisos


def etiqueta_dia(fecha):
    hoy = dt.datetime.now(TZ).date()
    if fecha == hoy:
        return "Hoy"
    if fecha == hoy + dt.timedelta(days=1):
        return "Mañana"
    return f"{DIAS_ES[fecha.weekday()]} {fecha.day} {MESES_ES[fecha.month - 1]}"


# ───────────────────────── Windows ─────────────────────────
def hwnd_de(ventana):
    try:
        w = ventana.winfo_id()
        return ctypes.windll.user32.GetParent(w) or w
    except Exception:
        return None


def activar_escala():
    """Avisa a Windows que Agenda dibuja según la escala: texto nítido a 125 % o 150 %."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", ctypes.wintypes.RECT),
                ("rcWork", ctypes.wintypes.RECT), ("dwFlags", ctypes.c_ulong)]


def area_de_trabajo(x, y, ancho, alto):
    """Área útil (sin barra de tareas) del monitor más cercano al rectángulo dado."""
    try:
        user32 = ctypes.windll.user32
    except AttributeError:
        return None
    user32.MonitorFromRect.restype = ctypes.c_void_p
    user32.GetMonitorInfoW.argtypes = (ctypes.c_void_p, ctypes.POINTER(_MONITORINFO))
    rect = ctypes.wintypes.RECT(x, y, x + ancho, y + alto)
    monitor = user32.MonitorFromRect(ctypes.byref(rect), 2)  # MONITOR_DEFAULTTONEAREST
    info = _MONITORINFO()
    info.cbSize = ctypes.sizeof(info)
    if not monitor or not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return None
    w = info.rcWork
    return w.left, w.top, w.right, w.bottom


def encajar(x, y, ancho, area, margen_cabecera=40):
    """Si la cabecera del widget no se ve lo suficiente dentro del área, devuelve una
    posición corregida dentro de ella; si se ve bien, devuelve None."""
    izq, arr, der, aba = area
    visible_x = min(x + ancho, der) - max(x, izq)
    visible_y = min(y + margen_cabecera, aba) - max(y, arr)
    if visible_x >= min(ancho, 80) and visible_y >= 30:
        return None
    nx = max(izq, min(x, der - ancho))
    ny = max(arr, min(y, aba - 120))
    return nx, ny


def ruta_recurso(nombre):
    """Ruta de un archivo incluido en el .exe (o junto al script)."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, nombre)


def escuchar_atajo(al_presionar, resultado):
    """Registra Ctrl+Alt+A en todo Windows. Corre en un hilo propio."""
    try:
        user32 = ctypes.windll.user32
    except AttributeError:
        resultado.append(False)
        return
    MOD_ALT, MOD_CONTROL, MOD_NOREPEAT, WM_HOTKEY = 0x1, 0x2, 0x4000, 0x0312
    ok = bool(user32.RegisterHotKey(None, 1, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, ord("A")))
    resultado.append(ok)
    if not ok:
        return
    msg = ctypes.wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        if msg.message == WM_HOTKEY:
            user32.AllowSetForegroundWindow(-1)  # ASFW_ANY
            al_presionar()


def traer_al_frente(ventana):
    ventana.attributes("-topmost", True)
    ventana.lift()
    hwnd = hwnd_de(ventana)
    if hwnd:
        ctypes.windll.user32.SetForegroundWindow(hwnd)
    ventana.focus_force()


def enviar_al_fondo(ventana):
    """Deja la ventana debajo de las demás aplicaciones."""
    ventana.attributes("-topmost", False)
    hwnd = hwnd_de(ventana)
    if hwnd:
        HWND_BOTTOM, SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 1, 0x1, 0x2, 0x10
        ctypes.windll.user32.SetWindowPos(hwnd, HWND_BOTTOM, 0, 0, 0, 0,
                                          SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE)


def bordes_redondeados(ventana):
    """Esquinas redondeadas de Windows 11 (se ignora en versiones anteriores)."""
    hwnd = hwnd_de(ventana)
    if hwnd:
        try:
            valor = ctypes.c_int(2)  # DWMWCP_ROUND
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(valor),
                                                       ctypes.sizeof(valor))
        except Exception:
            pass


# ───────────────────────── ventanas auxiliares ─────────────────────────
def boton(padre, texto, comando, primario=False, **kw):
    return tk.Button(padre, text=texto, command=comando, bd=0, cursor="hand2",
                     bg=ACENTO if primario else "#2A2A2A",
                     fg="#111111" if primario else FG,
                     activebackground="#E6B600" if primario else "#3A3A3A",
                     activeforeground="#111111" if primario else FG,
                     font=(FUENTE, 10 if primario else 9, "bold" if primario else "normal"),
                     padx=px(12), pady=px(6 if primario else 5), **kw)


class Ventana(tk.Toplevel):
    """Ventana oscura con título; base del administrador, la ayuda y el Acerca de."""
    def __init__(self, widget, titulo):
        super().__init__(widget.root)
        self.widget = widget
        self.title(f"{titulo} — {APP}")
        self.configure(bg=BG, padx=px(18), pady=px(16))
        self.resizable(False, False)
        self.attributes("-topmost", True)
        self.protocol("WM_DELETE_WINDOW", self.cerrar)
        self.bind("<Escape>", lambda e: self.cerrar())

    def pie(self):
        tk.Frame(self, bg="#2A2A2A", height=1).pack(fill="x", pady=(px(16), px(12)))
        boton(self, "Listo", self.cerrar, primario=True, width=12).pack(anchor="e")

    def cerrar(self):
        self.destroy()


class FilaCalendario(tk.Frame):
    """Un calendario: ✓ (confirmar nombre) · color · nombre editable · ✕."""
    def __init__(self, admin, indice):
        super().__init__(admin.marco_lista, bg=CAMPO)
        self.admin = admin
        self.indice = indice
        self.cal = admin.widget.cfg["calendarios"][indice]
        self._ocultar = None

        self.tick = tk.Label(self, text="", width=2, bg=CAMPO, fg=ACENTO,
                             font=(FUENTE, 11, "bold"))
        self.tick.pack(side="left", padx=(6, 0))
        self.tick.bind("<Button-1>", lambda e: self.confirmar())

        self.punto = tk.Label(self, bg=CAMPO, font=(FUENTE, 11), cursor="hand2")
        self.punto.pack(side="left", padx=(2, 6))
        self.punto.bind("<Button-1>", lambda e: self.alternar_visible())
        self.pintar_punto()

        self.var = tk.StringVar(value=self.cal["nombre"])
        self.campo = tk.Entry(self, textvariable=self.var, width=34, bg=CAMPO, fg=FG,
                              insertbackground=FG, bd=0, font=(FUENTE, 10),
                              highlightthickness=1, highlightbackground=CAMPO,
                              highlightcolor="#444444")
        self.campo.pack(side="left", fill="x", expand=True, ipady=px(5))
        self.campo.bind("<Return>", lambda e: self.confirmar())
        self.var.trace_add("write", lambda *a: self.al_escribir())

        quitar = tk.Label(self, text="✕", bg=CAMPO, fg=DIM, cursor="hand2",
                          font=(FUENTE, 10), padx=10)
        quitar.pack(side="right")
        quitar.bind("<Button-1>", lambda e: admin.quitar(self.indice))
        quitar.bind("<Enter>", lambda e: quitar.configure(fg="#E57373"))
        quitar.bind("<Leave>", lambda e: quitar.configure(fg=DIM))

    def pintar_punto(self):
        visible = self.cal.get("visible", True)
        self.punto.configure(text="●" if visible else "○",
                             fg=self.cal.get("color", ACENTO) if visible else DIM)

    def alternar_visible(self):
        self.cal["visible"] = not self.cal.get("visible", True)
        self.pintar_punto()
        guardar_cfg(self.admin.widget.cfg)
        self.admin.widget.actualizar()

    def pendiente(self):
        nuevo = self.var.get().strip()
        return bool(nuevo) and nuevo != self.cal["nombre"]

    def al_escribir(self):
        if self._ocultar:
            self.after_cancel(self._ocultar)
            self._ocultar = None
        if self.pendiente():
            self.tick.configure(text="✓", fg=ACENTO, cursor="hand2")
        else:
            self.tick.configure(text="", cursor="")

    def confirmar(self):
        if not self.pendiente():
            return False
        self.cal["nombre"] = self.var.get().strip()
        guardar_cfg(self.admin.widget.cfg)
        self.tick.configure(text="✓", fg=VERDE, cursor="")
        self._ocultar = self.after(1800, lambda: self.tick.configure(text=""))
        self.admin.widget.actualizar()
        return True


class Administrador(Ventana):
    def __init__(self, widget):
        super().__init__(widget, "Calendarios")

        tk.Label(self, text="CALENDARIOS", bg=BG, fg=ACENTO,
                 font=(FUENTE, 9, "bold"), anchor="w").pack(fill="x")
        tk.Label(self, text="Hacé clic en un nombre para cambiarlo y confirmá con ✓ o Enter. "
                            "El punto de color muestra u oculta el calendario.",
                 bg=BG, fg=DIM, font=(FUENTE, 9), anchor="w", justify="left",
                 wraplength=px(400)).pack(fill="x", pady=(2, px(8)))

        self.marco_lista = tk.Frame(self, bg=BG)
        self.marco_lista.pack(fill="x")
        self.filas = []

        tk.Label(self, text="Agregar: pegá la «Dirección secreta en formato iCal»",
                 bg=BG, fg=DIM, font=(FUENTE, 9), anchor="w").pack(fill="x", pady=(16, 2))
        self.url = tk.Entry(self, width=50, bg=CAMPO, fg=FG, insertbackground=FG,
                            bd=0, font=(FUENTE, 10))
        self.url.pack(fill="x", ipady=px(5))
        tk.Label(self, text="Nombre (opcional; si queda vacío se toma de Google)",
                 bg=BG, fg=DIM, font=(FUENTE, 9), anchor="w").pack(fill="x", pady=(8, 2))
        self.nombre = tk.Entry(self, width=50, bg=CAMPO, fg=FG, insertbackground=FG,
                               bd=0, font=(FUENTE, 10))
        self.nombre.pack(fill="x", ipady=px(5))
        self.boton = boton(self, "Agregar calendario", self.agregar)
        self.boton.pack(anchor="w", pady=(10, 0))
        self.url.bind("<Return>", lambda e: self.agregar())
        self.nombre.bind("<Return>", lambda e: self.agregar())

        self.pie()
        self.refrescar_lista()
        self.url.focus_set()

    def refrescar_lista(self):
        for f in self.filas:
            f.destroy()
        self.filas = []
        calendarios = self.widget.cfg["calendarios"]
        if not calendarios:
            vacio = tk.Label(self.marco_lista, text="Todavía no agregaste calendarios.",
                             bg=CAMPO, fg=DIM, font=(FUENTE, 10), anchor="w", padx=10, pady=8)
            vacio.pack(fill="x")
            self.filas.append(vacio)
            return
        for i in range(len(calendarios)):
            fila = FilaCalendario(self, i)
            fila.pack(fill="x", pady=(0, 2))
            self.filas.append(fila)

    def quitar(self, indice):
        c = self.widget.cfg["calendarios"][indice]
        if messagebox.askyesno("Quitar calendario", f"¿Quitar «{c['nombre']}» del widget?",
                               parent=self):
            self.guardar_pendientes()
            borrar_cache(c["url"])
            del self.widget.cfg["calendarios"][indice]
            guardar_cfg(self.widget.cfg)
            self.refrescar_lista()
            self.widget.actualizar()

    def guardar_pendientes(self):
        for f in self.filas:
            if isinstance(f, FilaCalendario):
                f.confirmar()

    def cerrar(self):
        self.guardar_pendientes()
        self.destroy()

    def agregar(self):
        url = self.url.get().strip()
        if not url:
            return
        if any(c["url"] == url for c in self.widget.cfg["calendarios"]):
            messagebox.showinfo(APP, "Ese calendario ya está agregado.", parent=self)
            return
        self.boton.configure(text="Verificando…", state="disabled")

        def tarea():
            try:
                cal = descargar(url)
                self.after(0, lambda: self.agregado(url, cal))
            except Exception:
                self.after(0, self.fallo)
        threading.Thread(target=tarea, daemon=True).start()

    def agregado(self, url, cal):
        self.guardar_pendientes()
        usados = {c.get("color") for c in self.widget.cfg["calendarios"]}
        color = next((p for p in PALETA if p not in usados),
                     PALETA[len(self.widget.cfg["calendarios"]) % len(PALETA)])
        nombre = self.nombre.get().strip() or nombre_del_calendario(cal) or "Calendario"
        self.widget.cfg["calendarios"].append(
            {"nombre": nombre, "url": url, "color": color, "visible": True})
        guardar_cfg(self.widget.cfg)
        self.url.delete(0, "end")
        self.nombre.delete(0, "end")
        self.boton.configure(text="Agregar calendario", state="normal")
        self.refrescar_lista()
        self.widget.actualizar()

    def fallo(self):
        self.boton.configure(text="Agregar calendario", state="normal")
        messagebox.showerror(APP, "No pude leer esa dirección.\n"
                             "Revisá que sea la «Dirección secreta en formato iCal» "
                             "y que haya conexión.", parent=self)


AYUDA = [
    ("h", "Agregar un calendario"),
    ("p", "1. Abrí Google Calendar en el navegador (calendar.google.com).\n"
          "2. Tocá el engranaje ⚙ → Configuración.\n"
          "3. En la columna izquierda, bajo «Configuración de mis calendarios», "
          "elegí el calendario.\n"
          "4. Bajá hasta «Integrar el calendario» y copiá la "
          "«Dirección secreta en formato iCal».\n"
          "5. En el widget: clic derecho → Administrar calendarios → pegala y tocá "
          "«Agregar calendario».\n"
          "Repetí con cada calendario que quieras ver."),
    ("h", "Mostrar, ocultar, renombrar y quitar"),
    ("p", "• Clic derecho → Calendarios: tildá los que querés ver.\n"
          "• En Administrar calendarios, el punto de color también los muestra u oculta.\n"
          "• Hacé clic en un nombre, escribí el nuevo y confirmá con ✓ o Enter.\n"
          "• La ✕ quita el calendario del widget (no lo borra de Google)."),
    ("h", "Traer el widget al frente"),
    ("p", "El widget vive detrás de tus aplicaciones. Con {atajo} aparece adelante; "
          "volvé a presionarlo, o hacé clic en otra ventana, y regresa al fondo."),
    ("h", "Otros ajustes"),
    ("p", "• Arrastralo con el mouse para moverlo; recuerda la posición.\n"
          "• Arrastrá su borde inferior para fijar el alto máximo. Si los eventos no entran, "
          "desplazá la lista con la rueda del mouse o con la barra fina de la derecha.\n"
          "• La hora de arriba a la derecha es la de la última actualización; "
          "hacé clic en ella para actualizar.\n"
          "• Clic derecho → Días a mostrar / Opacidad.\n"
          "• Se actualiza solo cada 10 minutos, o con clic derecho → Actualizar."),
    ("h", "Bueno saber"),
    ("p", "• Si no hay conexión, Agenda muestra los últimos eventos que pudo descargar y "
          "lo indica arriba, junto con la hora de la última actualización correcta. "
          "Vuelve a intentar cada 2 minutos.\n"
          "• Google puede demorar varias horas en publicar los cambios en la dirección "
          "iCal; un evento recién creado quizá no aparezca enseguida.\n"
          "• La dirección secreta da acceso de lectura a tu calendario: no la compartas. "
          "Si se filtra, en Google podés restablecerla y volver a agregarla acá.\n"
          "• La configuración se guarda en %APPDATA%\\Agenda y la copia de los eventos "
          "en %LOCALAPPDATA%\\Agenda; quitar un calendario borra su copia."),
]


class Ayuda(Ventana):
    def __init__(self, widget):
        super().__init__(widget, "Ayuda")
        texto = tk.Text(self, width=62, height=30, bg=BG, fg=FG, bd=0, wrap="word",
                        font=(FUENTE, 10), cursor="arrow", highlightthickness=0,
                        spacing1=1, spacing3=1)
        texto.tag_configure("h", font=(FUENTE, 11, "bold"), foreground=ACENTO,
                            spacing1=12, spacing3=4)
        texto.tag_configure("p", foreground=FG, lmargin1=0, lmargin2=14)
        for tipo, contenido in AYUDA:
            texto.insert("end", contenido.format(atajo=ATAJO_TEXTO) + "\n", tipo)
        if not widget.atajo_ok:
            texto.insert("end", f"\nAviso: {ATAJO_TEXTO} está ocupado por otra aplicación, "
                                "así que el atajo no está activo.\n", "p")
        texto.configure(state="disabled")
        texto.pack(fill="both")
        self.pie()


class AcercaDe(Ventana):
    def __init__(self, widget):
        super().__init__(widget, "Acerca de")
        tk.Label(self, text=APP.upper(), bg=BG, fg=ACENTO,
                 font=(FUENTE, 22, "bold"), anchor="w").pack(fill="x")
        tk.Label(self, text=f"Versión {VERSION}", bg=BG, fg=DIM,
                 font=(FUENTE, 9), anchor="w").pack(fill="x")
        tk.Label(self, text="Widget de escritorio para Windows 11 que muestra los próximos "
                            "eventos de tus calendarios de Google.",
                 bg=BG, fg=FG, font=(FUENTE, 10), anchor="w", justify="left",
                 wraplength=px(340)).pack(fill="x", pady=(px(12), px(10)))
        for etiqueta, valor in (("Autor", "Javier Benítez · Arquitectuna"),
                                ("Datos", "Dirección iCal de Google Calendar, solo lectura"),
                                ("Atajo", f"{ATAJO_TEXTO} para traerlo al frente"),
                                ("Hecho con", "Python y Tkinter · licencia MIT")):
            fila = tk.Frame(self, bg=BG)
            fila.pack(fill="x", pady=1)
            tk.Label(fila, text=etiqueta, width=10, bg=BG, fg=DIM, font=(FUENTE, 9),
                     anchor="w").pack(side="left")
            tk.Label(fila, text=valor, bg=BG, fg=FG, font=(FUENTE, 9),
                     anchor="w").pack(side="left")
        self.pie()


# ───────────────────────── widget ─────────────────────────
class Widget:
    def __init__(self):
        self.cfg = cargar_cfg()
        self.cola = queue.Queue()
        self.timer = None
        self.admin = None
        self.ultimos = None
        self.solicitud = 0          # número de la última actualización pedida
        self.ventanas = {}
        self.al_frente = False
        self.menu_abierto = False
        self._resultado_atajo = []

        self.root = tk.Tk()
        global E
        E = max(1.0, self.root.winfo_fpixels("1i") / 96)
        self.root.title(APP)
        self.root.overrideredirect(True)
        try:
            self.root.iconbitmap(default=ruta_recurso("agenda.ico"))
        except tk.TclError:
            pass
        self.root.configure(bg=BG)
        self.root.attributes("-alpha", self.cfg["opacidad"])
        self.root.geometry(f"+{self.cfg['x']}+{self.cfg['y']}")
        tk.Frame(self.root, bg=BG, width=px(ANCHO), height=0).pack()  # ancho fijo

        # borde inferior: se arrastra para fijar el alto máximo
        self.asa = tk.Canvas(self.root, bg=BG, width=1, height=px(8), highlightthickness=0, bd=0,
                             cursor="sb_v_double_arrow")
        self.asa.pack(side="bottom", fill="x")
        self.asa.bind("<Enter>", lambda e: self.pintar_asa(True))
        self.asa.bind("<Leave>", lambda e: self.pintar_asa(False))
        self.asa.bind("<Button-1>", self.asa_inicio)
        self.asa.bind("<B1-Motion>", self.asa_mover)
        self.asa.bind("<ButtonRelease-1>", self.asa_fin)

        self.marco = tk.Frame(self.root, bg=BG, padx=px(16))
        self.marco.pack(fill="both", expand=True, pady=(px(14), 0))

        cabecera = tk.Frame(self.marco, bg=BG)
        cabecera.pack(fill="x", pady=(0, px(4)))
        tk.Label(cabecera, text="AGENDA", bg=BG, fg=ACENTO,
                 font=(FUENTE, 9, "bold")).pack(side="left")
        self._hora_txt = ""
        self._hora_color = DIM
        self.lbl_hora = tk.Label(cabecera, text="", bg=BG, fg=DIM, font=(FUENTE, 8),
                                 cursor="hand2")
        self.lbl_hora.pack(side="right")
        self.lbl_hora.bind("<Button-1>", lambda e: self.actualizar())
        self.lbl_hora.bind("<Enter>", lambda e: self.lbl_hora.configure(
            text="↻ Actualizar ahora", fg=FG))
        self.lbl_hora.bind("<Leave>", lambda e: self.lbl_hora.configure(
            text=self._hora_txt, fg=self._hora_color))

        # lista de eventos con desplazamiento
        self.lienzo = tk.Canvas(self.marco, bg=BG, highlightthickness=0, bd=0,
                                width=px(ANCHO - 32), height=10, yscrollincrement=px(20))
        self.lienzo.pack(fill="x")
        self.cuerpo = tk.Frame(self.lienzo, bg=BG)
        self.lienzo.create_window(0, 0, window=self.cuerpo, anchor="nw", width=px(ANCHO - 32))
        self.cuerpo.bind("<Configure>", lambda e: self.ajustar_alto())
        self.desborda = False

        # barra fina estilo teléfono: aparece al desplazar o al pasar el mouse
        self.barra = tk.Canvas(self.marco, bg=BG, width=px(6), highlightthickness=0, bd=0)
        self.barra.bind("<Button-1>", self.barra_inicio)
        self.barra.bind("<B1-Motion>", self.barra_mover)
        self.barra.bind("<ButtonRelease-1>", self.barra_fin)
        self.barra.bind("<Enter>", lambda e: self.mostrar_barra(activa=True))
        self.barra.bind("<Leave>", lambda e: self.mostrar_barra())
        self._ocultar_barra = None
        self._arrastrando_barra = False
        self.no_arrastrar = (self.barra, self.asa, self.lbl_hora)

        self.root.bind_all("<Button-1>", self.iniciar_arrastre)
        self.root.bind_all("<B1-Motion>", self.arrastrar)
        self.root.bind_all("<ButtonRelease-1>", self.soltar)
        self.root.bind_all("<Button-3>", self.abrir_menu)
        self.root.bind_all("<MouseWheel>", self.rueda)
        self.root.bind_all("<Motion>", self.al_mover_mouse)
        self.root.bind("<FocusOut>", self.al_perder_foco)

        threading.Thread(target=escuchar_atajo, daemon=True,
                         args=(lambda: self.cola.put(("atajo", None, None)), self._resultado_atajo)
                         ).start()

        self.root.after(100, lambda: (bordes_redondeados(self.root), enviar_al_fondo(self.root)))
        self.root.after(400, self.vigilar_posicion)
        self.pintar_mensaje("Cargando…")
        self.actualizar()
        self.revisar_cola()

    # ── datos ──
    def programar(self, minutos):
        if self.timer:
            self.root.after_cancel(self.timer)
        self.timer = self.root.after(int(minutos * 60 * 1000), self.actualizar)

    def actualizar(self):
        # cada pedido lleva un número; solo se muestra el resultado del último,
        # así una descarga lenta anterior no pisa a una más nueva
        self.solicitud += 1
        numero = self.solicitud
        self.programar(self.cfg["refresco_min"])
        calendarios = [dict(c) for c in self.cfg["calendarios"]]
        dias = self.cfg["dias"]
        self.poner_hora("Actualizando…")

        def tarea():
            try:
                self.cola.put(("ok", numero, leer_eventos(calendarios, dias)))
            except Exception:
                self.cola.put(("error", numero, None))
        threading.Thread(target=tarea, daemon=True).start()

    def revisar_cola(self):
        try:
            while True:
                tipo, numero, dato = self.cola.get_nowait()
                if tipo == "atajo":
                    self.alternar_frente()
                elif numero != self.solicitud:
                    continue                      # resultado viejo: se descarta
                elif tipo == "ok":
                    self.ultimos = dato
                    self.pintar(*dato)
                    if any(e["estado"] != "ok" for e in dato[1]):
                        self.programar(REINTENTO_MIN)
                else:
                    self.pintar_mensaje("Error al leer los calendarios.")
                    self.poner_hora("Sin actualizar", ROJO)
                    self.programar(REINTENTO_MIN)
        except queue.Empty:
            pass
        self.root.after(150, self.revisar_cola)

    # ── interfaz ──
    def poner_hora(self, texto, color=DIM):
        self._hora_txt = texto
        self._hora_color = color
        self.lbl_hora.configure(text=texto, fg=color)

    def limpiar(self):
        for w in self.cuerpo.winfo_children():
            w.destroy()

    def texto(self, contenido, color=DIM):
        tk.Label(self.cuerpo, text=contenido, bg=BG, fg=color, font=(FUENTE, 10),
                 justify="left", anchor="w", wraplength=px(ANCHO - 32)).pack(fill="x", pady=(px(4), 0))

    def pintar_mensaje(self, contenido):
        self.limpiar()
        self.lienzo.yview_moveto(0)
        self.texto(contenido)

    def pintar(self, eventos, estados):
        posicion = self.lienzo.yview()[0]
        self.limpiar()
        cabecera, color, avisos = resumen_estado(estados)
        self.poner_hora(cabecera, color)
        self.root.after(60, lambda: self.lienzo.yview_moveto(posicion))
        for aviso in avisos:
            self.texto(aviso, color=color)
        if not self.cfg["calendarios"]:
            self.texto("Sin calendarios.\nClic derecho → Administrar calendarios.")
            return
        if not any(c.get("visible", True) for c in self.cfg["calendarios"]):
            self.texto("Todos los calendarios están ocultos.\nClic derecho → Calendarios.")
            return

        ahora = dt.datetime.now(TZ)
        dia_actual = None
        for ev in eventos[:MAX_EVENTOS]:
            if ev["dia"] != dia_actual:
                dia_actual = ev["dia"]
                tk.Label(self.cuerpo, text=etiqueta_dia(dia_actual), bg=BG, fg=FG,
                         font=(FUENTE, 11, "bold"), anchor="w").pack(fill="x", pady=(px(10), px(2)))
            pasado = not ev["todo_dia"] and ev["fin"] < ahora
            fila = tk.Frame(self.cuerpo, bg=BG)
            fila.pack(fill="x", pady=px(1))
            tk.Label(fila, text="●", bg=BG, fg=DIM if pasado else ev["color"],
                     font=(FUENTE, 8)).pack(side="left", anchor="n", pady=(px(3), 0))
            hora = "todo el día" if ev["todo_dia"] else ev["inicio"].strftime("%H:%M")
            tk.Label(fila, text=hora, width=9, bg=BG, fg=DIM,
                     font=(FUENTE, 9), anchor="w").pack(side="left", anchor="n", padx=(px(4), 0))
            tk.Label(fila, text=ev["titulo"], bg=BG, fg=DIM if pasado else FG,
                     font=(FUENTE, 10), anchor="w", justify="left",
                     wraplength=px(ANCHO - 125)).pack(side="left", fill="x")

        con_datos = any(e["estado"] != "sin_datos" for e in estados)
        if not eventos and con_datos:
            self.texto(f"Nada en los próximos {self.cfg['dias']} días.")
        elif not con_datos:
            self.texto(f"Se volverá a intentar en {REINTENTO_MIN} minutos.")
        elif len(eventos) > MAX_EVENTOS:
            self.texto(f"+ {len(eventos) - MAX_EVENTOS} eventos más")

    # ── alto y desplazamiento ──
    def ajustar_alto(self):
        contenido = self.cuerpo.winfo_reqheight()
        alto = max(10, min(contenido, px(self.cfg.get("alto_max", ALTO_BASE))))
        self.lienzo.configure(height=alto, scrollregion=(0, 0, px(ANCHO - 32), contenido))
        self.desborda = contenido > alto + 1
        if not self.desborda:
            self.lienzo.yview_moveto(0)
            self.barra.place_forget()
        elif self.barra.winfo_ismapped():
            self.mostrar_barra()

    def mostrar_barra(self, activa=False):
        if not self.desborda:
            return
        alto = int(self.lienzo.cget("height"))
        arriba, abajo = self.lienzo.yview()
        largo = max(px(28), int((abajo - arriba) * alto))
        y = int(arriba * alto)
        y = min(y, alto - largo)
        self.barra.delete("all")
        color = BARRA_ACTIVA if (activa or self._arrastrando_barra) else BARRA
        self.barra.create_line(px(3), px(4), px(3), largo - px(4), width=px(4), capstyle="round",
                               fill=color)
        self.barra.place(in_=self.lienzo, relx=1.0, x=px(5), y=y, width=px(6), height=largo)
        if self._ocultar_barra:
            self.root.after_cancel(self._ocultar_barra)
        self._ocultar_barra = self.root.after(1300, self.esconder_barra)

    def esconder_barra(self):
        self._ocultar_barra = None
        if self._arrastrando_barra:
            return
        x, y = self.root.winfo_pointerxy()
        if self.root.winfo_containing(x, y) is self.barra:
            self._ocultar_barra = self.root.after(800, self.esconder_barra)
            return
        self.barra.place_forget()

    def rueda(self, e):
        if not self.es_mio(e) or not self.desborda:
            return
        self.lienzo.yview_scroll(-2 * int(e.delta / 120) or (-1 if e.delta > 0 else 1), "units")
        self.mostrar_barra()

    def al_mover_mouse(self, e):
        if self.desborda and self.es_mio(e) and not self.barra.winfo_ismapped():
            self.mostrar_barra()

    def barra_inicio(self, e):
        self._arrastrando_barra = True
        self._barra_y0 = e.y_root
        self._barra_arriba0 = self.lienzo.yview()[0]

    def barra_mover(self, e):
        alto = int(self.lienzo.cget("height"))
        self.lienzo.yview_moveto(self._barra_arriba0 + (e.y_root - self._barra_y0) / alto)
        self.mostrar_barra()

    def barra_fin(self, e):
        self._arrastrando_barra = False
        self.mostrar_barra()

    def pintar_asa(self, visible):
        self.asa.delete("all")
        if visible:
            centro = px(ANCHO) // 2
            self.asa.create_line(centro - px(18), px(4), centro + px(18), px(4), width=px(3),
                                 capstyle="round", fill=BARRA)

    def asa_inicio(self, e):
        self._asa_y0 = e.y_root
        self._asa_alto0 = int(self.lienzo.cget("height"))

    def asa_mover(self, e):
        tope = self.root.winfo_screenheight() - 160
        nuevo = self._asa_alto0 + (e.y_root - self._asa_y0)
        # se guarda en píxeles de diseño, para que valga igual con otra escala
        self.cfg["alto_max"] = int(max(ALTO_MIN, min(nuevo, tope) / E))
        self.ajustar_alto()

    def asa_fin(self, e):
        guardar_cfg(self.cfg)

    def restablecer_alto(self):
        self.cfg["alto_max"] = ALTO_BASE
        guardar_cfg(self.cfg)
        self.ajustar_alto()

    # ── menú ──
    def abrir_menu(self, e):
        if not self.es_mio(e):
            return
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Actualizar", command=self.actualizar)

        sub = tk.Menu(menu, tearoff=0)
        self._vars = []
        for i, c in enumerate(self.cfg["calendarios"]):
            v = tk.BooleanVar(value=c.get("visible", True))
            self._vars.append(v)
            sub.add_checkbutton(label=c["nombre"], variable=v,
                                command=lambda i=i, v=v: self.cambiar_visible(i, v.get()))
        if not self.cfg["calendarios"]:
            sub.add_command(label="(ninguno)", state="disabled")
        menu.add_cascade(label="Calendarios", menu=sub)
        menu.add_command(label="Administrar calendarios…",
                         command=lambda: self.abrir_ventana(Administrador))

        dias = tk.Menu(menu, tearoff=0)
        self._dias = tk.IntVar(value=self.cfg["dias"])
        for d in (1, 3, 7, 14):
            dias.add_radiobutton(label=f"{d} día" + ("" if d == 1 else "s"), value=d,
                                 variable=self._dias, command=self.cambiar_dias)
        menu.add_cascade(label="Días a mostrar", menu=dias)

        op = tk.Menu(menu, tearoff=0)
        self._op = tk.DoubleVar(value=self.cfg["opacidad"])
        for p in (1.0, 0.9, 0.8, 0.7, 0.6):
            op.add_radiobutton(label=f"{int(p * 100)} %", value=p, variable=self._op,
                               command=self.cambiar_opacidad)
        menu.add_cascade(label="Opacidad", menu=op)
        menu.add_command(label="Restablecer alto", command=self.restablecer_alto)

        menu.add_separator()
        menu.add_command(label="Ayuda", command=lambda: self.abrir_ventana(Ayuda))
        menu.add_command(label=f"Acerca de {APP}", command=lambda: self.abrir_ventana(AcercaDe))
        menu.add_separator()
        menu.add_command(label="Salir", command=self.root.destroy)
        self.menu_abierto = True
        try:
            menu.tk_popup(e.x_root, e.y_root)
        finally:
            menu.grab_release()
            self.root.after(300, lambda: setattr(self, "menu_abierto", False))

    def cambiar_visible(self, i, visible):
        self.cfg["calendarios"][i]["visible"] = visible
        guardar_cfg(self.cfg)
        self.actualizar()

    def cambiar_dias(self):
        self.cfg["dias"] = self._dias.get()
        guardar_cfg(self.cfg)
        self.actualizar()

    def cambiar_opacidad(self):
        self.cfg["opacidad"] = self._op.get()
        self.root.attributes("-alpha", self.cfg["opacidad"])
        guardar_cfg(self.cfg)

    def abrir_ventana(self, clase):
        v = self.ventanas.get(clase)
        if v is not None and v.winfo_exists():
            v.lift()
            v.focus_force()
            return
        self.ventanas[clase] = clase(self)

    @property
    def atajo_ok(self):
        return bool(self._resultado_atajo and self._resultado_atajo[0])

    # ── al frente / al fondo ──
    def alternar_frente(self):
        if self.al_frente:
            self.al_fondo()
        else:
            self.al_frente = True
            traer_al_frente(self.root)

    def al_fondo(self):
        self.al_frente = False
        enviar_al_fondo(self.root)

    def al_perder_foco(self, e):
        if self.al_frente:
            self.root.after(150, self._verificar_foco)

    def _verificar_foco(self):
        # focus_get() es None cuando el foco pasó a otra aplicación
        if self.al_frente and not self.menu_abierto and self.root.focus_get() is None:
            self.al_fondo()

    # ── mover ──
    def es_mio(self, e):
        try:
            return e.widget.winfo_toplevel() is self.root
        except Exception:
            return False

    def iniciar_arrastre(self, e):
        if not self.es_mio(e) or e.widget in self.no_arrastrar:
            return
        self._dx = e.x_root - self.root.winfo_x()
        self._dy = e.y_root - self.root.winfo_y()

    def arrastrar(self, e):
        if not self.es_mio(e) or e.widget in self.no_arrastrar or not hasattr(self, "_dx"):
            return
        self.root.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def soltar(self, e):
        if not self.es_mio(e) or e.widget in self.no_arrastrar:
            return
        self.cfg["x"], self.cfg["y"] = self.root.winfo_x(), self.root.winfo_y()
        guardar_cfg(self.cfg)
        if not self.al_frente:
            enviar_al_fondo(self.root)

    def vigilar_posicion(self):
        """Si cambió la configuración de pantallas y el widget quedó fuera de la vista,
        lo trae de vuelta. Se revisa cada 30 segundos."""
        try:
            x, y = self.root.winfo_x(), self.root.winfo_y()
            ancho, alto = self.root.winfo_width(), self.root.winfo_height()
            area = area_de_trabajo(x, y, ancho, alto)
            nueva = encajar(x, y, ancho, area) if area else None
            if nueva:
                self.root.geometry(f"+{nueva[0]}+{nueva[1]}")
                self.cfg["x"], self.cfg["y"] = nueva
                guardar_cfg(self.cfg)
                if not self.al_frente:
                    enviar_al_fondo(self.root)
        finally:
            self.root.after(30000, self.vigilar_posicion)

    def correr(self):
        self.root.mainloop()


if __name__ == "__main__":
    # evita abrir dos copias del widget a la vez
    try:
        ctypes.windll.kernel32.CreateMutexW(None, False, "Agenda_GCal_Widget")
        if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            raise SystemExit
    except AttributeError:
        pass
    activar_escala()
    Widget().correr()
