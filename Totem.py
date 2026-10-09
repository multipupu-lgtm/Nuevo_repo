import machine
import time

# ==========================================
# MASTER RS485 - búsqueda de disponibilidad, guardar y retirar
# ==========================================

uart = machine.UART(0, baudrate=9600, tx=machine.Pin(0), rx=machine.Pin(1), timeout=3000)
rs485_ctrl = machine.Pin(2, machine.Pin.OUT)
rs485_ctrl.value(0)  # Modo escucha

# Pines de entrada de inicio de operación (la confirmación ahora es por consola)
pin_13 = machine.Pin(13, machine.Pin.IN, machine.Pin.PULL_DOWN)  # inicio guardar
pin_14 = machine.Pin(14, machine.Pin.IN, machine.Pin.PULL_DOWN)  # inicio retiro placa 1
pin_15 = machine.Pin(15, machine.Pin.IN, machine.Pin.PULL_DOWN)  # inicio retiro placa 2

MAX_INTENTOS = 3
TIMEOUT_RESPUESTA_MS = 10000
TIMEOUT_OPERACION_MS = 130000  # 60 s por fase (activación y cierre) + margen
RETRY_DELAY_MS = 1500

# Descubrimiento automatico de esclavos (solo al arranque)
MAX_ID_SONDEO = 4          # respaldo secuencial: IDs 1..MAX_ID_SONDEO
ROUNDS_BROADCAST = 2       # rondas de broadcast *:Descubrir
TIMEOUT_DESCUBRIMIENTO_MS = 1000   # ventana de escucha por ronda
TIMEOUT_SONDEO_MS = 400            # timeout del sondeo secuencial
ESCLAVOS_ACTIVOS = []      # se llena en el arranque con descubrir_esclavos()


def limpiar_buffer_uart():
    """Borra los datos viejos del UART antes de consultar."""
    while uart.any():
        uart.read()
        time.sleep_ms(10)


def datos_a_texto(datos):
    """Convierte datos UART a texto sin lanzar errores en MicroPython."""
    if datos is None:
        return None
    if isinstance(datos, (bytes, bytearray)):
        data = bytes(datos)
        try:
            return data.decode('utf-8').strip()
        except Exception:
            try:
                return ''.join(chr(b) for b in data if b < 128).strip()
            except Exception:
                return str(data).strip()
    if isinstance(datos, str):
        return datos.strip()
    return str(datos).strip()


def leer_respuesta_rs485(esclavo_id, comando_esperado, timeout_ms=TIMEOUT_RESPUESTA_MS):
    """Lee y valida la respuesta esperada del esclavo."""
    inicio = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), inicio) < timeout_ms:
        if not uart.any():
            time.sleep_ms(10)
            continue
        datos = uart.readline()
        if not datos:
            continue

        respuesta = datos_a_texto(datos)
        if respuesta is None:
            continue

        if not respuesta.startswith(f"{esclavo_id}:"):
            print(f"[Maestro] Respuesta descartada: {respuesta} (no corresponde a placa {esclavo_id})")
            continue

        contenido = respuesta.split(":", 1)[1].strip()

        if comando_esperado == "Disponible":
            if contenido in ("Disponible", "Ocupado", "Error", "RetireSuBicicleta"):
                return respuesta
        elif comando_esperado == "Guardar":
            if contenido in ("Guardada", "Error", "Disponible"):
                return respuesta
        elif comando_esperado == "Retirar":
            if contenido in ("Retirada", "Disponible", "Error"):
                return respuesta
        else:
            return respuesta

        print(f"[Maestro] Respuesta descartada: {respuesta} (esperaba {comando_esperado} para placa {esclavo_id})")

    return None


def enviar_consulta(esclavo_id, comando, timeout_ms=TIMEOUT_RESPUESTA_MS):
    """Envía una sola consulta y espera la respuesta del comando solicitado."""
    limpiar_buffer_uart()

    rs485_ctrl.value(1)
    time.sleep_ms(20)

    payload = f"{esclavo_id}:{comando}\n"
    uart.write(payload.encode('utf-8'))

    while not uart.txdone():
        pass

    time.sleep_ms(30)
    rs485_ctrl.value(0)
    time.sleep_ms(80)

    print(f"\n[Maestro] Consulta -> Placa {esclavo_id}. Comando: {comando}")

    respuesta = leer_respuesta_rs485(esclavo_id, comando, timeout_ms)
    if respuesta is not None:
        print(f"[Maestro] Respuesta del bus: {respuesta}")
        return respuesta

    print(f"[Maestro] La placa {esclavo_id} no respondió con la respuesta esperada para '{comando}'.")
    return None


def respuesta_presente_valida(texto):
    """Valida una respuesta de descubrimiento con formato exacto ID:Presente."""
    if not texto or ":" not in texto:
        return False
    prefijo = texto.split(":", 1)[0]
    return prefijo.isdigit() and 0 < len(prefijo) <= 3 and texto == f"{prefijo}:Presente"


def descubrir_broadcast(timeout_ms=TIMEOUT_DESCUBRIMIENTO_MS):
    """Envía *:Descubrir al bus y colecta respuestas ID:Presente."""
    limpiar_buffer_uart()

    rs485_ctrl.value(1)
    time.sleep_ms(20)
    uart.write("*:Descubrir\n".encode('utf-8'))
    while not uart.txdone():
        pass
    time.sleep_ms(30)
    rs485_ctrl.value(0)
    time.sleep_ms(50)

    print(f"[Maestro] Broadcast '*:Descubrir' enviado. Ventana de {timeout_ms} ms...")

    encontrados = set()
    fin = time.ticks_add(time.ticks_ms(), timeout_ms)
    while time.ticks_diff(fin, time.ticks_ms()) > 0:
        if uart.any():
            datos = uart.readline()
            texto = datos_a_texto(datos)
            if texto and respuesta_presente_valida(texto):
                sid = texto.split(":", 1)[0]
                if sid not in encontrados:
                    encontrados.add(sid)
                    print(f"[Maestro] Recibido: {sid}:Presente")
        else:
            time.sleep_ms(10)
    return encontrados


def sondeo_secuencial(encontrados):
    """Respaldo: sondea los IDs 1..MAX_ID_SONDEO que no respondieron al broadcast."""
    for indice in range(1, MAX_ID_SONDEO + 1):
        sid = str(indice)
        if sid in encontrados:
            continue
        print(f"[Maestro] Sondeo secuencial de la placa {sid}...")
        respuesta = enviar_consulta(sid, "Estado", TIMEOUT_SONDEO_MS)
        if respuesta is not None and respuesta.startswith(f"{sid}:"):
            encontrados.add(sid)
            print(f"[Maestro] Placa {sid} detectada por sondeo secuencial.")
    return encontrados


def descubrir_esclavos():
    """Híbrido: broadcast con jitter en los esclavos + sondeo secuencial de respaldo."""
    encontrados = set()

    for ronda in range(1, ROUNDS_BROADCAST + 1):
        print(f"\n[Maestro] Descubrimiento automatico (ronda {ronda}/{ROUNDS_BROADCAST})")
        encontrados |= descubrir_broadcast()
        time.sleep_ms(200)

    sondeo_secuencial(encontrados)

    lista = sorted(encontrados, key=int)
    if lista:
        print(f"[Maestro] Placas detectadas: {', '.join(lista)}")
    else:
        print("[Maestro] No se detecto ninguna placa en el bus RS485.")
    return lista


def buscar_placa_disponible(placa_id=None):
    """Busca disponibilidad. Si se pasa placa_id, solo revisa esa placa."""
    ids = [placa_id] if placa_id else list(ESCLAVOS_ACTIVOS)

    if not ids:
        print("\n[Maestro] No hay placas detectadas en el bus RS485.")
        return None

    for placa in ids:
        print(f"\n[Maestro] Revisando disponibilidad de la placa {placa}...")
        for intento in range(1, MAX_INTENTOS + 1):
            respuesta = enviar_consulta(placa, "Disponible")

            if respuesta is not None and "Disponible" in respuesta:
                print(f"[Maestro] Placa disponible detectada: {placa}")
                return placa

            if respuesta is not None and "Ocupado" in respuesta:
                print(f"[Maestro] Placa {placa} está ocupada.")
                break

            if intento < MAX_INTENTOS:
                print(f"[Maestro] Esperando siguiente intento para placa {placa}...")
                time.sleep_ms(RETRY_DELAY_MS)

    print("[Maestro] No se encuentran placas disponibles.")
    return None


def confirmar_por_consola(placa_id, accion):
    """Menú de confirmación por consola serie. True si el usuario confirma."""
    etiqueta = "Guardar" if accion == "Guardar" else "Retirar"
    while True:
        print("\n========================================")
        print("Confirme operación")
        print(f"s - {etiqueta} en la placa {placa_id}")
        print("n - Cancelar")
        print("========================================")
        valor = input().strip().lower()
        if valor in ("s", "si", "sí"):
            return True
        if valor in ("n", "no"):
            print(f"[Maestro] Operación de {etiqueta.lower()} cancelada.")
            return False
        print("Valor inválido. Ingrese s o n.")


def esperar_confirmacion_guardado(placa_id):
    if not confirmar_por_consola(placa_id, "Guardar"):
        return

    respuesta_guardar = enviar_consulta(placa_id, "Guardar", TIMEOUT_OPERACION_MS)
    if respuesta_guardar is None:
        print(f"[Maestro] Timeout de guardado para placa {placa_id}.")
        return

    if "Guardada" in respuesta_guardar:
        print(f"[Maestro] La placa {placa_id} confirmó que el guardado fue exitoso.")
    elif "Error" in respuesta_guardar:
        print(f"[Maestro] La placa {placa_id} respondió con error al guardar.")
    else:
        print(f"[Maestro] La placa {placa_id} respondió algo inesperado: {respuesta_guardar}")


def esperar_confirmacion_retiro(placa_id):
    if not confirmar_por_consola(placa_id, "Retirar"):
        return

    respuesta_retirar = enviar_consulta(placa_id, "Retirar", TIMEOUT_OPERACION_MS)
    if respuesta_retirar is None:
        print(f"[Maestro] Timeout de retiro para placa {placa_id}.")
        return

    if "Retirada" in respuesta_retirar:
        print(f"[Maestro] La placa {placa_id} confirmó que el retiro fue exitoso.")
    elif "Disponible" in respuesta_retirar:
        print(f"[Maestro] La placa {placa_id} ya quedó disponible.")
    elif "Error" in respuesta_retirar:
        print(f"[Maestro] La placa {placa_id} respondió con error al retirar.")
    else:
        print(f"[Maestro] La placa {placa_id} respondió algo inesperado: {respuesta_retirar}")


def mostrar_menu():
    print("\n========================================")
    print("Menu principal")
    print("Placas detectadas: " + (", ".join(ESCLAVOS_ACTIVOS) if ESCLAVOS_ACTIVOS else "ninguna"))
    print("1 - Guardar")
    print("2 - Retirar")
    print("3 - Salir")
    print("========================================")


def mostrar_menu_placa():
    print("\n========================================")
    print("Seleccione la placa")
    if ESCLAVOS_ACTIVOS:
        for sid in ESCLAVOS_ACTIVOS:
            print(f"{sid} - Placa {sid}")
    else:
        print("(no hay placas detectadas)")
    print("========================================")


def seleccionar_placa_por_consola():
    while True:
        try:
            mostrar_menu_placa()
            valor = input()
            valor = valor.strip()
            if valor in ESCLAVOS_ACTIVOS:
                return valor
            if ESCLAVOS_ACTIVOS:
                print("Valor invalido. Placas detectadas: " + ", ".join(ESCLAVOS_ACTIVOS))
            else:
                print("No hay placas detectadas. Verifique el bus RS485.")
        except Exception:
            return None


def seleccionar_accion_por_consola():
    while True:
        try:
            mostrar_menu()
            valor = input()
            valor = valor.strip()
            if valor in ("1", "2", "3"):
                return valor
            print("Opción inválida. Ingrese 1, 2 o 3.")
        except Exception:
            return None


def ejecutar_accion_en_placa(placa_id, accion):
    if accion not in ("Guardar", "Retirar"):
        print(f"[Maestro] Acción inválida: {accion}")
        return

    if placa_id not in ESCLAVOS_ACTIVOS:
        print(f"[Maestro] La placa {placa_id} no fue detectada en el bus RS485.")
        return

    print(f"\n[Maestro] Consulta de disponibilidad para {accion.lower()} en placa {placa_id}...")
    respuesta = enviar_consulta(placa_id, "Disponible")
    if respuesta is None:
        print(f"[Maestro] La placa {placa_id} no respondió a la consulta de disponibilidad.")
        return

    if accion == "Guardar":
        if "Disponible" in respuesta:
            print(f"[Maestro] Placa {placa_id} disponible para guardar.")
            esperar_confirmacion_guardado(placa_id)
        else:
            print(f"[Maestro] La placa {placa_id} no está disponible para guardar.")
    else:
        if "Ocupado" in respuesta:
            print(f"[Maestro] Placa {placa_id} ocupada y lista para retirar.")
            esperar_confirmacion_retiro(placa_id)
        else:
            print(f"[Maestro] La placa {placa_id} no está ocupada para retirar.")


# ==================== ARRANQUE: DESCUBRIMIENTO DE ESCLAVOS ====================
print("\n[Maestro] Buscando esclavos en el bus RS485...")
ESCLAVOS_ACTIVOS = descubrir_esclavos()

if not ESCLAVOS_ACTIVOS:
    print("[Maestro] No se detectaron placas. Reintentando en 3 s...")
    time.sleep(3)
    ESCLAVOS_ACTIVOS = descubrir_esclavos()

print(f"[Maestro] Placas en servicio: {', '.join(ESCLAVOS_ACTIVOS) if ESCLAVOS_ACTIVOS else 'NINGUNA'}\n")


while True:
    if pin_13.value() == 1:
        print("\n========================================")
        print("[Maestro] Inicio de guardar (GP13 = HIGH)")
        print("========================================")

        placa_disponible = buscar_placa_disponible()

        if placa_disponible is None:
            print("[Maestro] No se encuentran placas disponibles para guardar.")
        else:
            print(f"[Maestro] Se encontró una placa disponible: {placa_disponible}")
            esperar_confirmacion_guardado(placa_disponible)

        while pin_13.value() == 1:
            time.sleep_ms(50)
        continue

    if pin_14.value() == 1:
        print("\n========================================")
        print("[Maestro] Retiro solicitado en placa 1 (GP14 = HIGH)")
        print("========================================")

        placa = "1"
        if placa not in ESCLAVOS_ACTIVOS:
            print(f"[Maestro] Placa {placa} no detectada en el bus RS485.")
        else:
            respuesta = enviar_consulta(placa, "Disponible")
            if respuesta is not None and "Ocupado" in respuesta:
                print(f"[Maestro] Placa {placa} ocupada y lista para retirar.")
                esperar_confirmacion_retiro(placa)
            else:
                print(f"[Maestro] La placa {placa} no está ocupada para retirar.")

        while pin_14.value() == 1:
            time.sleep_ms(50)
        continue

    if pin_15.value() == 1:
        print("\n========================================")
        print("[Maestro] Retiro solicitado en placa 2 (GP15 = HIGH)")
        print("========================================")

        placa = "2"
        if placa not in ESCLAVOS_ACTIVOS:
            print(f"[Maestro] Placa {placa} no detectada en el bus RS485.")
        else:
            respuesta = enviar_consulta(placa, "Disponible")
            if respuesta is not None and "Ocupado" in respuesta:
                print(f"[Maestro] Placa {placa} ocupada y lista para retirar.")
                esperar_confirmacion_retiro(placa)
            else:
                print(f"[Maestro] La placa {placa} no está ocupada para retirar.")

        while pin_15.value() == 1:
            time.sleep_ms(50)
        continue

    try:
        accion = seleccionar_accion_por_consola()
        if accion is None:
            time.sleep_ms(200)
            continue

        if accion == "1":
            placa = seleccionar_placa_por_consola()
            if placa is not None:
                ejecutar_accion_en_placa(placa, "Guardar")
            else:
                print("[Maestro] Se canceló la selección de placa.")

        elif accion == "2":
            placa = seleccionar_placa_por_consola()
            if placa is not None:
                ejecutar_accion_en_placa(placa, "Retirar")
            else:
                print("[Maestro] Se canceló la selección de placa.")

        elif accion == "3":
            print("[Maestro] Salida del menú.")
            break

    except Exception as e:
        print(f"[Maestro] Error en menú: {e}")

    time.sleep_ms(100)
