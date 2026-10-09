import machine
import time
import random

# !!! CONFIGURACIÓN INDIVIDUAL: cambiar en cada placa !!!
#   Placa 1 -> MI_ID = "1"
#   Placa 2 -> MI_ID = "2"
MI_ID = "1"

# RS485 en GP2
uart = machine.UART(0, baudrate=9600, tx=machine.Pin(0), rx=machine.Pin(1), timeout=2000)
rs485_ctrl = machine.Pin(2, machine.Pin.OUT)
rs485_ctrl.value(0)  # Escuchando

# Salidas del guardado y retiro:
# reposo en alta impedancia (Z) y activación llevando el pin a nivel lógico 0.
pin_3 = machine.Pin(3, machine.Pin.IN)
pin_4 = machine.Pin(4, machine.Pin.IN)

# Entradas de lectura de estado
pin_6 = machine.Pin(6, machine.Pin.IN, machine.Pin.PULL_DOWN)
pin_7 = machine.Pin(7, machine.Pin.IN, machine.Pin.PULL_DOWN)
pin_8 = machine.Pin(8, machine.Pin.IN, machine.Pin.PULL_DOWN)
pin_9 = machine.Pin(9, machine.Pin.IN, machine.Pin.PULL_UP)

TIEMPO_ESPERA_MS = 60000   # tope por fase (activación y cierre): al menos 1 minuto
TIEMPO_ENTRE_LECTURAS_MS = 20

# Retardo anti-colisión para el broadcast *:Descubrir.
# El base es fijo por placa (derivado del número de serie) para que dos
# esclavos nunca respondan a la vez; la ventana aleatoria varía por ronda.
_UNICIDAD = int.from_bytes(machine.unique_id(), "big")
RETARDO_BASE_MS = _UNICIDAD % 600
VENTANA_ALEATORIA_MS = 150
random.seed((_UNICIDAD ^ time.ticks_ms()) & 0x7FFFFFFF)


def datos_a_texto(datos):
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


def estado_disponibilidad():
    p6 = pin_6.value()
    p7 = pin_7.value()
    p8 = pin_8.value()
    p9 = pin_9.value()

    if p6 == 1 and p7 == 1 and p8 == 0 and p9 == 1:
        return "Disponible"

    if p6 == 0 and p7 == 0 and p8 == 1 and p9 == 0:
        return "Ocupado"

    if p6 == 0 and p7 == 0 and p8 == 0 and p9 == 1:
        return "RetireSuBicicleta"

    return "Error"


def enviar_respuesta(texto):
    """Envía la respuesta al bus RS485 y vuelve a Modo RX."""
    rs485_ctrl.value(1)
    time.sleep_ms(20)
    uart.write((texto + "\n").encode('utf-8'))

    while not uart.txdone():
        pass

    time.sleep_ms(30)
    rs485_ctrl.value(0)
    time.sleep_ms(50)


def confirmar_activacion_guardado():
    p6 = pin_6.value()
    p7 = pin_7.value()
    p8 = pin_8.value()
    p9 = pin_9.value()
    return (p6 == 0 and p7 == 0 and p8 == 0 and p9 == 1)


def confirmar_cierre_guardado():
    p6 = pin_6.value()
    p7 = pin_7.value()
    p8 = pin_8.value()
    p9 = pin_9.value()
    return (p6 == 0 and p7 == 0 and p8 == 1 and p9 == 0)


def activar_salida(pin):
    """Activa la salida llevándola a nivel lógico 0 (sink)."""
    pin.init(machine.Pin.OUT, value=0)


def reposo_salida(pin):
    """Devuelve la salida a alta impedancia pura (Pin.IN, sin pull)."""
    pin.init(machine.Pin.IN)


def proceso_guardado():
    """Valida la secuencia completa y responde al maestro solo al final."""
    print("Guardado solicitado. Validando secuencia de entradas...")

    reposo_salida(pin_3)
    inicio = time.ticks_ms()

    while time.ticks_diff(time.ticks_ms(), inicio) < TIEMPO_ESPERA_MS:
        if confirmar_activacion_guardado():
            print("Confirmación de activación: 6=0, 7=0, 8=0, 9=1")
            activar_salida(pin_3)

            cierre_inicio = time.ticks_ms()
            while time.ticks_diff(time.ticks_ms(), cierre_inicio) < TIEMPO_ESPERA_MS:
                if confirmar_cierre_guardado():
                    reposo_salida(pin_3)
                    print("Confirmación de cierre: 6=0, 7=0, 8=1, 9=0")
                    time.sleep_ms(20)
                    enviar_respuesta(f"{MI_ID}:Guardada")
                    print("Respuesta de guardado enviada al maestro.")
                    return
                time.sleep_ms(TIEMPO_ENTRE_LECTURAS_MS)

            reposo_salida(pin_3)
            print("Timeout en cierre. Se vuelve a escuchar.")
            enviar_respuesta(f"{MI_ID}:Error")
            return

        time.sleep_ms(TIEMPO_ENTRE_LECTURAS_MS)

    reposo_salida(pin_3)
    print("Timeout de 60 s. Se regresa al inicio y vuelve a esperar consulta.")
    enviar_respuesta(f"{MI_ID}:Error")


def proceso_retirado():
    """Valida la secuencia de retiro: 6=0, 7=0, 8=1, 9=0 y luego 6=0, 7=0, 8=0, 9=1."""
    print("Retiro solicitado. Validando secuencia de entradas...")

    reposo_salida(pin_4)
    inicio = time.ticks_ms()

    while time.ticks_diff(time.ticks_ms(), inicio) < TIEMPO_ESPERA_MS:
        p6 = pin_6.value()
        p7 = pin_7.value()
        p8 = pin_8.value()
        p9 = pin_9.value()

        if p6 == 0 and p7 == 0 and p8 == 1 and p9 == 0:
            print("Confirmación de retiro: 6=0, 7=0, 8=1, 9=0")
            activar_salida(pin_4)

            cierre_inicio = time.ticks_ms()
            while time.ticks_diff(time.ticks_ms(), cierre_inicio) < TIEMPO_ESPERA_MS:
                p6 = pin_6.value()
                p7 = pin_7.value()
                p8 = pin_8.value()
                p9 = pin_9.value()

                if p6 == 0 and p7 == 0 and p8 == 0 and p9 == 1:
                    reposo_salida(pin_4)
                    print("Secuencia de retiro confirmada: 6=0, 7=0, 8=0, 9=1")
                    time.sleep_ms(20)
                    enviar_respuesta(f"{MI_ID}:Retirada")
                    print("Respuesta de retiro enviada al maestro.")
                    return

                if p6 == 1 and p7 == 1 and p8 == 0 and p9 == 1:
                    reposo_salida(pin_4)
                    print("Bicicleta disponible: 6=1, 7=1, 8=0, 9=1")
                    time.sleep_ms(20)
                    enviar_respuesta(f"{MI_ID}:Disponible")
                    return

                time.sleep_ms(TIEMPO_ENTRE_LECTURAS_MS)

            reposo_salida(pin_4)
            print("Timeout en retiro. Se vuelve a escuchar.")
            enviar_respuesta(f"{MI_ID}:Error")
            return

        if p6 == 0 and p7 == 0 and p8 == 0 and p9 == 1:
            print("Retire su bicicleta: 6=0, 7=0, 8=0, 9=1")
            enviar_respuesta(f"{MI_ID}:Error")
            return

        if p6 == 1 and p7 == 1 and p8 == 0 and p9 == 1:
            print("La bicicleta está disponible: 6=1, 7=1, 8=0, 9=1")
            enviar_respuesta(f"{MI_ID}:Disponible")
            return

        time.sleep_ms(TIEMPO_ENTRE_LECTURAS_MS)

    reposo_salida(pin_4)
    print("Timeout de retiro. Error.")
    enviar_respuesta(f"{MI_ID}:Error")


print(f"Esclavo {MI_ID} activo. Retardo de descubrimiento: {RETARDO_BASE_MS}+0..{VENTANA_ALEATORIA_MS} ms")
print("Monitoreando pines 6, 7, 8 y 9...")

while True:
    if uart.any():
        linea = uart.readline()
        if linea:
            try:
                contenido = datos_a_texto(linea)
                if contenido is None:
                    continue

                print("Recibido:", contenido)

                if ":" not in contenido:
                    continue

                id_destino, mensaje_recibido = contenido.split(":", 1)
                mensaje_recibido = mensaje_recibido.strip()

                if id_destino not in (MI_ID, "*"):
                    continue

                if mensaje_recibido == "Descubrir":
                    if id_destino == "*":
                        espera = RETARDO_BASE_MS + random.randint(0, VENTANA_ALEATORIA_MS)
                        print(f"Descubrimiento recibido. Respondiendo en {espera} ms...")
                        time.sleep_ms(espera)
                    enviar_respuesta(f"{MI_ID}:Presente")
                    print(f"Respuesta de descubrimiento enviada: {MI_ID}:Presente")

                elif mensaje_recibido == "Disponible":
                    estado = estado_disponibilidad()
                    texto_respuesta = f"{MI_ID}:{estado}"
                    enviar_respuesta(texto_respuesta)
                    print(f"Respuesta enviada: '{texto_respuesta}'")

                elif mensaje_recibido == "Guardar":
                    if estado_disponibilidad() == "Disponible":
                        proceso_guardado()
                    else:
                        enviar_respuesta(f"{MI_ID}:Error")

                elif mensaje_recibido == "Retirar":
                    estado = estado_disponibilidad()
                    if estado == "Ocupado":
                        proceso_retirado()
                    elif estado == "Disponible":
                        enviar_respuesta(f"{MI_ID}:Disponible")
                    elif estado == "RetireSuBicicleta":
                        print("Retire su bicicleta: 6=0, 7=0, 8=0, 9=1")
                        enviar_respuesta(f"{MI_ID}:Error")
                    else:
                        enviar_respuesta(f"{MI_ID}:Error")

                elif mensaje_recibido == "Estado":
                    estado = estado_disponibilidad()
                    enviar_respuesta(f"{MI_ID}:{estado}")

            except Exception:
                rs485_ctrl.value(0)
                print("Error en UART; modo escucha restaurado.")

    time.sleep_ms(100)
