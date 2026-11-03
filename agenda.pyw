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
import json
import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import messagebox

import icalendar
import recurring_ical_events
import requests

APP = "Agenda"
VERSION = "1.2"
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

CFG_BASE = {
    "calendarios": [],      # [{"nombre", "url", "color", "visible"}]
    "x": 60,
    "y": 60,
    "dias": 7,
    "refresco_min": 10,
    "opacidad": 0.85,
}

BG = "#111111"
FG = "#F2F2F2"
DIM = "#7A7A7A"
ACENTO = "#FFCA01"
CAMPO = "#1C1C1C"
VERDE = "#81C784"
FUENTE = "Segoe UI"
ANCHO = 320
MAX_EVENTOS = 40
PALETA = ["#FFCA01", "#4FC3F7", "#81C784", "#F48FB1", "#CE93D8", "#FFAB91", "#B0BEC5", "#FFF59D"]

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
def descargar(url):
    url = url.strip()
    if url.lower().startswith("webcal://"):
        url = "https://" + url[9:]
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    return icalendar.Calendar.from_ical(r.content)


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


def leer_eventos(calendarios, dias):
    desde = dt.datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    hasta = desde + dt.timedelta(days=dias)
    eventos, errores = [], []
    for c in calendarios:
        if not c.get("visible", True):
            continue
        try:
            cal = descargar(c["url"])
            eventos += eventos_de(cal, c.get("color", ACENTO), desde, hasta)
        except Exception:
            errores.append(c.get("nombre", "calendario"))
    eventos.sort(key=lambda e: (e["dia"], not e["todo_dia"], e["inicio"], e["titulo"]))
    return eventos, errores


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
                     padx=12, pady=6 if primario else 5, **kw)


class Ventana(tk.Toplevel):
    """Ventana oscura con título; base del administrador, la ayuda y el Acerca de."""
    def __init__(self, widget, titulo):
        super().__init__(widget.root)
        self.widget = widget
        self.title(f"{titulo} — {APP}")
        self.configure(bg=BG, padx=18, pady=16)
        self.resizable(False, False)
        self.attributes("-topmost", True)
        self.protocol("WM_DELETE_WINDOW", self.cerrar)
        self.bind("<Escape>", lambda e: self.cerrar())

    def pie(self):
        tk.Frame(self, bg="#2A2A2A", height=1).pack(fill="x", pady=(16, 12))
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
        self.campo.pack(side="left", fill="x", expand=True, ipady=5)
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
                 wraplength=400).pack(fill="x", pady=(2, 8))

        self.marco_lista = tk.Frame(self, bg=BG)
        self.marco_lista.pack(fill="x")
        self.filas = []

        tk.Label(self, text="Agregar: pegá la «Dirección secreta en formato iCal»",
                 bg=BG, fg=DIM, font=(FUENTE, 9), anchor="w").pack(fill="x", pady=(16, 2))
        self.url = tk.Entry(self, width=50, bg=CAMPO, fg=FG, insertbackground=FG,
                            bd=0, font=(FUENTE, 10))
        self.url.pack(fill="x", ipady=5)
        tk.Label(self, text="Nombre (opcional; si queda vacío se toma de Google)",
                 bg=BG, fg=DIM, font=(FUENTE, 9), anchor="w").pack(fill="x", pady=(8, 2))
        self.nombre = tk.Entry(self, width=50, bg=CAMPO, fg=FG, insertbackground=FG,
                               bd=0, font=(FUENTE, 10))
        self.nombre.pack(fill="x", ipady=5)
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
          "• Clic derecho → Días a mostrar / Opacidad.\n"
          "• Se actualiza solo cada 10 minutos, o con clic derecho → Actualizar."),
    ("h", "Bueno saber"),
    ("p", "• Google puede demorar varias horas en publicar los cambios en la dirección "
          "iCal; un evento recién creado quizá no aparezca enseguida.\n"
          "• La dirección secreta da acceso de lectura a tu calendario: no la compartas. "
          "Si se filtra, en Google podés restablecerla y volver a agregarla acá.\n"
          "• La configuración se guarda en %APPDATA%\\Agenda."),
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
                 wraplength=340).pack(fill="x", pady=(12, 10))
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
        self.ventanas = {}
        self.al_frente = False
        self.menu_abierto = False
        self._resultado_atajo = []

        self.root = tk.Tk()
        self.root.title(APP)
        self.root.overrideredirect(True)
        try:
            self.root.iconbitmap(default=ruta_recurso("agenda.ico"))
        except tk.TclError:
            pass
        self.root.configure(bg=BG)
        self.root.attributes("-alpha", self.cfg["opacidad"])
        self.root.geometry(f"+{self.cfg['x']}+{self.cfg['y']}")
        tk.Frame(self.root, bg=BG, width=ANCHO, height=0).pack()  # ancho fijo

        self.cuerpo = tk.Frame(self.root, bg=BG, padx=16, pady=14)
        self.cuerpo.pack(fill="both", expand=True)

        self.root.bind_all("<Button-1>", self.iniciar_arrastre)
        self.root.bind_all("<B1-Motion>", self.arrastrar)
        self.root.bind_all("<ButtonRelease-1>", self.soltar)
        self.root.bind_all("<Button-3>", self.abrir_menu)
        self.root.bind("<FocusOut>", self.al_perder_foco)

        threading.Thread(target=escuchar_atajo, daemon=True,
                         args=(lambda: self.cola.put(("atajo", None)), self._resultado_atajo)
                         ).start()

        self.root.after(100, lambda: (bordes_redondeados(self.root), enviar_al_fondo(self.root)))
        self.pintar_mensaje("Cargando…")
        self.actualizar()
        self.revisar_cola()

    # ── datos ──
    def actualizar(self):
        if self.timer:
            self.root.after_cancel(self.timer)
        self.timer = self.root.after(self.cfg["refresco_min"] * 60 * 1000, self.actualizar)
        calendarios = [dict(c) for c in self.cfg["calendarios"]]
        dias = self.cfg["dias"]

        def tarea():
            try:
                self.cola.put(("ok", leer_eventos(calendarios, dias)))
            except Exception as exc:
                self.cola.put(("error", str(exc)))
        threading.Thread(target=tarea, daemon=True).start()

    def revisar_cola(self):
        try:
            while True:
                tipo, dato = self.cola.get_nowait()
                if tipo == "atajo":
                    self.alternar_frente()
                elif tipo == "ok":
                    self.ultimos = dato
                    self.pintar(*dato)
                else:
                    self.pintar_mensaje("Error al leer los calendarios.")
        except queue.Empty:
            pass
        self.root.after(150, self.revisar_cola)

    # ── interfaz ──
    def limpiar(self):
        for w in self.cuerpo.winfo_children():
            w.destroy()

    def encabezado(self):
        fila = tk.Frame(self.cuerpo, bg=BG)
        fila.pack(fill="x", pady=(0, 4))
        tk.Label(fila, text="AGENDA", bg=BG, fg=ACENTO,
                 font=(FUENTE, 9, "bold")).pack(side="left")
        tk.Label(fila, text=dt.datetime.now(TZ).strftime("%H:%M"), bg=BG, fg=DIM,
                 font=(FUENTE, 8)).pack(side="right")

    def texto(self, contenido, color=DIM):
        tk.Label(self.cuerpo, text=contenido, bg=BG, fg=color, font=(FUENTE, 10),
                 justify="left", anchor="w", wraplength=ANCHO - 32).pack(fill="x", pady=(4, 0))

    def pintar_mensaje(self, contenido):
        self.limpiar()
        self.encabezado()
        self.texto(contenido)

    def pintar(self, eventos, errores):
        self.limpiar()
        self.encabezado()
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
                         font=(FUENTE, 11, "bold"), anchor="w").pack(fill="x", pady=(10, 2))
            pasado = not ev["todo_dia"] and ev["fin"] < ahora
            fila = tk.Frame(self.cuerpo, bg=BG)
            fila.pack(fill="x", pady=1)
            tk.Label(fila, text="●", bg=BG, fg=DIM if pasado else ev["color"],
                     font=(FUENTE, 8)).pack(side="left", anchor="n", pady=(3, 0))
            hora = "todo el día" if ev["todo_dia"] else ev["inicio"].strftime("%H:%M")
            tk.Label(fila, text=hora, width=9, bg=BG, fg=DIM,
                     font=(FUENTE, 9), anchor="w").pack(side="left", anchor="n", padx=(4, 0))
            tk.Label(fila, text=ev["titulo"], bg=BG, fg=DIM if pasado else FG,
                     font=(FUENTE, 10), anchor="w", justify="left",
                     wraplength=ANCHO - 125).pack(side="left", fill="x")

        if not eventos:
            self.texto(f"Nada en los próximos {self.cfg['dias']} días.")
        elif len(eventos) > MAX_EVENTOS:
            self.texto(f"+ {len(eventos) - MAX_EVENTOS} eventos más")
        if errores:
            self.texto("Sin conexión: " + ", ".join(errores), color="#E57373")

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
        if not self.es_mio(e):
            return
        self._dx = e.x_root - self.root.winfo_x()
        self._dy = e.y_root - self.root.winfo_y()

    def arrastrar(self, e):
        if not self.es_mio(e) or not hasattr(self, "_dx"):
            return
        self.root.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def soltar(self, e):
        if not self.es_mio(e):
            return
        self.cfg["x"], self.cfg["y"] = self.root.winfo_x(), self.root.winfo_y()
        guardar_cfg(self.cfg)
        if not self.al_frente:
            enviar_al_fondo(self.root)

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
    Widget().correr()
