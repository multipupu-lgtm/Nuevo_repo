# Informe técnico del sistema de control del bicicleteiro automático

**Plataforma:** Raspberry Pi Pico (RP2040) — MicroPython v1.29.0
**Archivos descriptos:** `Uart_maestro/main.py`, `Uart1/main.py`, `Uart2/main.py`
**Versión del documento:** correspondiente a la versión vigente del código en el repositorio.

---

## Tabla de contenidos

1. [Descripción general del sistema](#1-descripción-general-del-sistema)
2. [Interfaz de hardware](#2-interfaz-de-hardware)
3. [Protocolo de comunicación RS485](#3-protocolo-de-comunicación-rs485)
4. [Lógica de la placa esclava](#4-lógica-de-la-placa-esclava)
5. [Lógica de la placa maestra](#5-lógica-de-la-placa-maestra)
6. [Secuencias temporales de ejemplo](#6-secuencias-temporales-de-ejemplo)
7. [Tabla resumen de parámetros](#7-tabla-resumen-de-parámetros)
8. [Consideraciones de diseño y limitaciones](#8-consideraciones-de-diseño-y-limitaciones)

---

## 1. Descripción general del sistema

El bicicleteiro automático es un sistema de guardado y retiro de bicicletas dotado de
cerrojo eléctrico (solenoides), que se controla de forma distribuida mediante tres
placas Raspberry Pi Pico programadas en MicroPython:

- **Una placa maestra** (`Uart_maestro/main.py`): administra la interfaz con el
  usuario (botones de inicio y consola serie), decide qué bahía atender y coordina
  todas las operaciones.
- **Dos placas esclavas** (`Uart1/main.py` y `Uart2/main.py`): una por bahía de
  bicicleta. Leen los sensores de estado, accionan los solenoides del cerrojo y
  responden a las órdenes del maestro.

Las tres placas se interconectan por un **bus RS485 semidúplex** (media línea: un
transceptor a la vez puede transmitir), lo que permite distancias y robustez
eléctricas superiores a las de un UART simple. Cada esclavo lleva una dirección
numérica única (`MI_ID = "1"` o `MI_ID = "2"`) y solo responde a las órdenes
dirigidas a ella o a las de difusión (broadcast).

### 1.1 Topología del sistema

```
                     +-----------------------+
                     |   PLACA MAESTRA       |
                     |   Uart_maestro/main.py|
                     |                       |
                     | GP0/GP1  UART0  TX/RX |
                     | GP2      DE/RE        |
                     | GP13/14/15 botones    |
                     +-----------+-----------+
                                 |
              +------------------+------------------+
              |    BUS RS485 (semidúplex)           |
              |    9600 baud, 8N1, dif. A/B         |
              |                                     |
   +----------+----------------+         +----------+----------------+
   | PLACA ESCLAVA 1           |         | PLACA ESCLAVA 2           |
   | Uart1/main.py             |         | Uart2/main.py             |
   | MI_ID = "1"               |         | MI_ID = "2"               |
   | GP0/GP1 UART0, GP2 DE/RE  |         | GP0/GP1 UART0, GP2 DE/RE  |
   | GP6..GP9 sensores         |         | GP6..GP9 sensores         |
   | GP3 cerrar, GP4 abrir     |         | GP3 cerrar, GP4 abrir     |
   +---------------------------+         +---------------------------+
         Bahía 1                                 Bahía 2
```

### 1.2 Evolución del sistema

En las primeras versiones del proyecto la comunicación se implementó con un protocolo
binario enmarcado (byte de inicio, dirección, comando y CRC16 Modbus) y, con
anterioridad a eso, con mensajes de texto simples sin validación. La versión vigente
adoptó un **protocolo de texto ASCII de bajo overhead** (`ID:Comando\n`) que conserva
la direccionabilidad y agrega el **descubrimiento automático de esclavos**, dejando
dicho marco de trabajo como el implementado en producción.

---

## 2. Interfaz de hardware

### 2.1 Placa maestra

| Pin | Dir. | Función | Configuración |
|---|---|---|---|
| GP0 | Salida | UART0 TX → transceptor RS485 | 9600 baud, timeout 3000 ms |
| GP1 | Entrada | UART0 RX ← transceptor RS485 | — |
| GP2 | Salida | Control DE/RE del transceptor | `1` = transmite, `0` = escucha |
| GP13 | Entrada | Botón **inicio de guardado** | `PULL_DOWN` (activo en alto) |
| GP14 | Entrada | Botón **inicio de retiro — bahía 1** | `PULL_DOWN` (activo en alto) |
| GP15 | Entrada | Botón **inicio de retiro — bahía 2** | `PULL_DOWN` (activo en alto) |

Los botones de la maestra solo **inician** una operación; la confirmación de las
operaciones destructivas (guardar/retirar) se realiza por consola serie (sección 5).

### 2.2 Placa esclava (idéntica en `Uart1` y `Uart2`, salvo `MI_ID`)

| Pin | Dir. | Función física | Reposo / nivel lógico |
|---|---|---|---|
| GP0 | Salida | UART0 TX → transceptor RS485 | 9600 baud, timeout 2000 ms |
| GP1 | Entrada | UART0 RX ← transceptor RS485 | — |
| GP2 | Salida | Control DE/RE del transceptor | `1` = transmite, `0` = escucha |
| **GP6** | Entrada | **(1)** Sensa la **presencia de bicicleta** | `PULL_DOWN`. Nivel `0` = con bicicleta; `1` = sin bicicleta |
| **GP7** | Entrada | **(2)** Sensa la **presencia del perno en el cerrojo** del bicicletero | `PULL_DOWN`. Nivel `0` = con perno; `1` = sin perno |
| **GP8** | Entrada | **(3)** Detecta el cambio de estado del solenoide del cerrojo → **cerrojo ABIERTO** | `PULL_DOWN`. Nivel `0` = cerrojo abierto detectado; `1` = no abierto |
| **GP9** | Entrada | **(4)** Detecta el cambio de estado del solenoide del cerrojo → **cerrojo CERRADO** | `PULL_UP`. Nivel `0` = cerrojo cerrado detectado; `1` = no cerrado |
| **GP3** | Salida | **(5)** Activación del solenoide para **CERRAR** la cerradura | Reposo en **alta impedancia (Z)**; activación llevando el pin a **nivel 0** |
| **GP4** | Salida | **(6)** Activación del solenoide para **ABRIR** la cerradura | Reposo en **alta impedancia (Z)**; activación llevando el pin a **nivel 0** |

Notas de diseño:

- **Convención de niveles de los sensores:** los cuatro entradas (GP6–GP9) operan con
  lógica **activa en bajo**: el nivel `0` indica la condición detectada (bicicleta
  presente, perno presente, cerrojo abierto, cerrojo cerrado respectivamente). Esta
  convención es la que hace consistente la tabla de estados (sección 4.2) con las
  secuencias físicas de guardado y retiro.
- **Salidas en alta impedancia:** en reposo GP3 y GP4 no drivean la línea
  (`machine.Pin.IN`, sin pull interno). La activación conmuta el pin a salida forzada
  en `0` (`machine.Pin.OUT, value=0`), de modo que la etapa externa (driver/optoacoplador
  del solenoide) es la que circula corriente hacia tierra (activación por hundimiento,
  típica de módulos optoacoplados de activación baja). Al finalizar la operación el
  pin vuelve a Z, garantizando reposo eléctrico total.
- **Doble detección del cerrojo:** GP8 y GP9 son complementarias en todos los estados
  válidos (nunca ambas activas): representan las dos posiciones del mecanismo
  (abierto/cerrado) tal como las reporta la etapa de detección del solenoide.

---

## 3. Protocolo de comunicación RS485

### 3.1 Capa física

| Parámetro | Valor |
|---|---|
| Estándar | RS485, semidúplex (dos hilos diferenciales + masa) |
| Velocidad | 9600 baud |
| Formato | 8 datos, sin paridad, 1 stop (8N1) |
| Transceptor | Módulo con entrada de control DE/RE unificados en **GP2** |
| Marco de aplicación | Texto ASCII terminado en `\n` (`0x0A`) |

Toda transmisión — tanto del maestro como de los esclavos — sigue la secuencia de
control de semidúplex:

```
GP2 = 1  ──┐ 20 ms  ┌── uart.write(...) ──┐ txdone  ┌── 30 ms ──┐ GP2 = 0
(transmite) │        │                     │         │           │ (escucha)
            └────────┘                     └─────────┘           └──── ...
                                                              (+50/80 ms de
                                                               guarda antes
                                                               de recibir)
```

Este esquema asegura que el transceptor vuelva a modo recepción **recién cuando el
último bit salió del UART**, evitando autorecepción y colisiones con la respuesta
del esclavo.

### 3.2 Trama de aplicación

```
+---------------+------------------+---------------------+------+
| Dirección     | ":"              | Comando / Respuesta | \n   |
+---------------+------------------+---------------------+------+
 "1" | "2" | "*"                    "Disponible", "Guardada"...
 (unicast) (broadcast)
```

- **Unicast:** `1:Guardar\n` — solo el esclavo con `MI_ID = "1"` actúa.
- **Broadcast:** `*:Descubrir\n` — todos los esclavos lo consideran (usado
  exclusivamente por el descubrimiento).
- Una trama sin el carácter `:` o dirigida a otra ID es **ignorada silenciosamente**
  por cada esclavo (el filtro es `id_destino not in (MI_ID, "*")`).

### 3.3 Catálogo de comandos y respuestas

| Comando (maestro → esclavo) | Respuesta (esclavo → maestro) | Significado |
|---|---|---|
| `ID:Descubrir` / `*:Descubrir` | `ID:Presente` | Descubrimiento / presencia en el bus |
| `ID:Disponible` | `ID:Disponible` \| `ID:Ocupado` \| `ID:RetireSuBicicleta` \| `ID:Error` | Consulta de estado de la bahía |
| `ID:Estado` | ídem | Consulta de estado (usada por el sondeo secuencial) |
| `ID:Guardar` | `ID:Guardada` \| `ID:Error` | Orden de guardado (respuesta al finalizar la secuencia) |
| `ID:Retirar` | `ID:Retirada` \| `ID:Disponible` \| `ID:Error` | Orden de retiro (respuesta al finalizar la secuencia) |

El maestro **valida** cada respuesta antes de aceptarla (`leer_respuesta_rs485`):
debe comenzar con el prefijo `ID:` de la placa consultada y el contenido debe
pertenecer al conjunto permitido para el comando enviado; el resto se descarta e
imprime `Respuesta descartada`.

### 3.4 Descubrimiento automático de esclavos

Ejecutado **solo al arranque** del maestro, en régimen híbrido: difusión con
retardo aleatorio (rápida) respaldada por sondeo secuencial (completa).

```
   MAESTRO                          ESCLAVO 1                 ESCLAVO 2
      |                                 |                          |
      |==== *:Descubrir  (ronda 1) ====>|=========================>|
      |                                 |                          |
      |                    (espera RETARDO_BASE_1 + rnd 0..150 ms) |
      |<========= 1:Presente ===========|                          |
      |                                 |  (espera RETARDO_BASE_2  |
      |                                 |        + rnd 0..150 ms)  |
      |<=============================================== 2:Presente |
      |   [ventana de escucha: 1000 ms]  |                          |
      |                                 |                          |
      |==== *:Descubrir  (ronda 2) ===> |  (repite, colecta IDs)   |
      |                                 |                          |
      |  Sondeo secuencial de respaldo: ID 1..MAX_ID_SONDEO        |
      |---- 3:Estado (400 ms) --------->|  (solo IDs no hallados)  |
      |---- 4:Estado (400 ms) --------->|                          |
      |                                 |                          |
      |  Si la lista quedó vacía: reintento completo a los 3 s     |
```

Mecanismos involucrados:

1. **Jitter anti-colisión:** al recibir el broadcast, cada esclavo espera
   `RETARDO_BASE_MS + random(0..150)` ms antes de responder, donde
   `RETARDO_BASE_MS = unique_id % 600` se deriva del **número de serie único** de
   cada Pico. Como dos placas tienen series distintas, sus retardos base no coinciden
   y las respuestas no colisionan; la componente aleatoria —resembrada por
   `unique_id ^ ticks_ms`— varía el retardo entre rondas.
2. **Validación de formato:** el maestro solo acepta respuestas con formato exacto
   `ID:Presente` (prefijo numérico de 1 a 3 cifras), de modo que tramas corruptas
   por ruido o colisión no ensucian la lista.
3. **Respaldo secuencial:** para los IDs `1..MAX_ID_SONDEO` (4) que no respondieron
   al broadcast, se envía un `Estado` con timeout corto (400 ms); si hay respuesta
   válida, la placa se agrega a la lista.
4. **Reintento:** si tras ambas fases no se detectó ninguna placa, el maestro espera
   3 s y repite el descubrimiento completo una vez más.

El resultado se guarda en la lista global `ESCLAVOS_ACTIVOS`, que alimenta el menú,
la búsqueda de bahías y la validación de todas las operaciones posteriores.

---

## 4. Lógica de la placa esclava

*Aplica por igual a `Uart1/main.py` (`MI_ID = "1"`) y `Uart2/main.py`
(`MI_ID = "2"`): los archivos son idénticos salvo por esa constante.*

### 4.1 Bucle principal

```
                    ┌──────────────────────┐
                    │      ARRANQUE        │
                    │ UART0, GP2=0 (RX),   │
                    │ GP3/GP4 en Z,        │
                    │ GP6-GP9 entradas     │
                    └──────────┬───────────┘
                               ▼
                 ┌─────────────────────────────┐
                 │  ¿uart.any()?  ── No ──┐    │
                 └──────────┬─────────────┘    │
                       Sí   ▼                  │
                 ┌──────────────────────┐      │
                 │ Leer línea, convertir│      │
                 │ a texto              │      │
                 └──────────┬───────────┘      │
                            ▼                  │
              ¿tiene ":" y destino             │
              es MI_ID o "*" ? ── No ──┐       │
                    Sí                 │       │
                    ▼                  │       │
        ┌──────────────────────────┐   │       │
        │ Despacho por comando:    │   │       │
        │  Descubrir               │   │       │
        │  Disponible              │   │       │
        │  Guardar                 │   │       │
        │  Retirar                 │   │       │
        │  Estado                  │   │       │
        └──────────┬───────────────┘   │       │
                   │ (respuesta o      │       │
                   │  secuencia larga) │       │
                   └───────────────────┴───┬───┘
                                           ▼
                                  sleep 100 ms ──► (vuelve arriba)
```

 ante cualquier excepción UART, el manejador `except` restaura `GP2 = 0`
(escucha) para que el bus nunca quede bloqueado en modo transmisión.

### 4.2 Tabla de estados de disponibilidad

`estado_disponibilidad()` lee los cuatro sensores y devuelve uno de cuatro estados:

| GP6 (bici) | GP7 (perno) | GP8 (abierto) | GP9 (cerrado) | Estado | Interpretación física |
|---|---|---|---|---|---|
| 1 | 1 | 0 | 1 | **Disponible** | Bahía **vacía**, sin perno, cerrojo **abierto**: lista para recibir una bicicleta |
| 0 | 0 | 1 | 0 | **Ocupado** | **Bicicleta y perno presentes**, cerrojo **cerrado**: guardado completo |
| 0 | 0 | 0 | 1 | **RetireSuBicicleta** | Bicicleta y perno presentes pero cerrojo **abierto**: el usuario debe retirarla |
| cualquier otra combinación | | | | **Error** | Combinación inválida de sensores (ruido, transición, falla) |

*(Niveles según la convención activa en bajo de la sección 2.2.)*

### 4.3 Secuencia de guardado (`proceso_guardado`)

Se ejecuta solo si, al recibir `Guardar`, el estado es `Disponible`. Fases con tope
de **60 s cada una** (`TIEMPO_ESPERA_MS`), muestreando cada **20 ms**: si el estado
de los pines cambia antes del tope, **la secuencia continúa de inmediato**.

```
   BAHÍA (sensores)            ESCLAVO                          BUS
   ----------------            -------                          ---
   Estado: Disponible          recibe 1:Guardar
   (1,1,0,1) vacío/abierto ──► activa fase 1
                               GP3 → Z (reinicio)
        │
        │  usuario pone bicicleta + perno
        ▼
   (0,0,0,1): bici+perno,      ✔ condición de ACTIVACIÓN
   cerrojo abierto ──────────► GP3 → 0  ◄── (punto 5: SOLenoide CIERRA)
        │                                     │
        │  solenoide acciona el cerrojo       │
        ▼                                     │
   (0,0,1,0): cerrojo          ✔ condición de CIERRE
   cerrado ──────────────────► GP3 → Z (reposo)
                               responde ─────► 1:Guardada
        ▲
        │ si NO se cumple la activación en 60 s
        │ o el cierre en 60 s:
        └──► GP3 → Z y responde 1:Error
```

Estados de salida de la función:

| Situación | Acción sobre GP3 | Respuesta al maestro |
|---|---|---|
| Cierre confirmado | vuelve a Z | `ID:Guardada` |
| Timeout en la fase de cierre (60 s) | vuelve a Z | `ID:Error` |
| Timeout en la fase de activación (60 s) | vuelve a Z | `ID:Error` |
| Estado inicial no era `Disponible` | sin cambios (queda en Z) | `ID:Error` (responde el despacho, sin entrar al proceso) |

### 4.4 Secuencia de retiro (`proceso_retirado`)

Se ejecuta solo si, al recibir `Retirar`, el estado es `Ocupado`.

```
   BAHÍA (sensores)            ESCLAVO                          BUS
   ----------------            -------                          ---
   Estado: Ocupado             recibe 1:Retirar
   (0,0,1,0) cerrado ────────► activa fase 1 (vuelve a verificar
                                el patrón (0,0,1,0); con la
                                precondición se cumple en el
                                primer muestreo)
                               GP4 → 0  ◄── (punto 6: solenoide ABRE)
        │
        │  solenoide abre el cerrojo
        ▼
   (0,0,0,1): bici+perno,      ✔ condición de APERTURA
   cerrojo abierto ──────────► GP4 → Z (reposo)
                               responde ─────► 1:Retirada
        ▲
        │ si el usuario ya extrajo todo durante la espera:
        │   (1,1,0,1) vacío/abierto ──► GP4 → Z, responde 1:Disponible
        │ si expira el tope de 60 s ──► GP4 → Z, responde 1:Error
``

Además, dentro de la fase 1 el bucle detecta dos situaciones especiales antes de
expirar el tiempo:

| Patrón detectado en fase 1 | Significado | Respuesta |
|---|---|---|
| `(0,0,0,1)` | Cerrojo ya abierto con la bicicleta dentro (retiro en curso) | `ID:Error` + aviso "Retire su bicicleta" |
| `(1,1,0,1)` | Bahía ya vacía y abierta (todo retirado) | `ID:Disponible` |

### 4.5 Despacho de comandos

| Comando recibido | Condición | Conducta |
|---|---|---|
| `Descubrir` (broadcast `*`) | — | Espera `RETARDO_BASE + rnd(0..150)` ms → responde `ID:Presente` |
| `Descubrir` (dirigido) | — | Responde `ID:Presente` de inmediato (sin retardo) |
| `Disponible` / `Estado` | — | Responde `ID:` + `estado_disponibilidad()` |
| `Guardar` | estado = `Disponible` | Ejecuta `proceso_guardado()` |
| `Guardar` | otro estado | Responde `ID:Error` sin tocar GP3 |
| `Retirar` | estado = `Ocupado` | Ejecuta `proceso_retirado()` |
| `Retirar` | estado = `Disponible` | Responde `ID:Disponible` (nada que retirar) |
| `Retirar` | estado = `RetireSuBicicleta` o `Error` | Responde `ID:Error` |

### 4.6 Funciones del script esclavo

| Función | Rol |
|---|---|
| `datos_a_texto()` | Convierte bytes UART a texto sin lanzar excepciones en MicroPython |
| `estado_disponibilidad()` | Mapea GP6–GP9 a los cuatro estados de la tabla 4.2 |
| `enviar_respuesta()` | Secuencia DE/RE semidúplex y transmisión de la respuesta |
| `confirmar_activacion_guardado()` | Valida el patrón `(0,0,0,1)` de la fase 1 del guardado |
| `confirmar_cierre_guardado()` | Valida el patrón `(0,0,1,0)` de la fase 2 del guardado |
| `activar_salida(pin)` | Conmuta GP3/GP4 a salida forzada en `0` (activación del solenoide) |
| `reposo_salida(pin)` | Devuelve GP3/GP4 a alta impedancia (`Pin.IN`, sin pull) |
| `proceso_guardado()` | Máquina de fases del guardado con timeouts de 60 s |
| `proceso_retirado()` | Máquina de fases del retiro con timeouts de 60 s y sus ramas alternativas |

---

## 5. Lógica de la placa maestra

### 5.1 Arranque

```
   Encendido / reset
        │
        ▼
   descubrir_esclavos()  (sección 3.4)
        │
        ├── lista vacía ──► esperar 3 s ──► descubrir_esclavos() otra vez
        ▼
   ESCLAVOS_ACTIVOS = ["1", "2", ...]
   imprime "Placas en servicio: ..."
        ▼
   bucle principal
```

### 5.2 Bucle principal — orden de prioridad

El bucle atiende en el siguiente orden (uno por pasada, con *debounce* de espera a
soltar el botón):

```
   1) GP13 = HIGH  ──► inicio de GUARDAR (busca bahía disponible sola)
   2) GP14 = HIGH  ──► inicio de RETIRO bahía 1
   3) GP15 = HIGH  ──► inicio de RETIRO bahía 2
   4) Sin botones  ──► menú por consola serie:
                         1 - Guardar
                         2 - Retirar
                         3 - Salir (termina el programa)
```

### 5.3 Flujo de guardado

```
   USUARIO                 MAESTRO                              ESCLAVO
   ------                  ------                               ------
   pulsar GP13  ──────►   buscar_placa_disponible():
                            para cada ID en ESCLAVOS_ACTIVOS:
                              consulta  ID:Disponible  ─────────► estado
                              ◄──────── ID:Disponible/ID:Ocupado
                              "Disponible" → la elige (fin de búsqueda)
                              "Ocupado"    → pasa a la siguiente
                              sin respuesta → reintenta (3 intentos,
                                              1,5 s entre intentos)
   ◄── "Confirme operación
        s - Guardar en la
        placa X / n - Cancelar"
   escribir "s"  ──────►  envía  ID:Guardar (timeout 130 s) ────► proceso_guardado
                                                          (fases de 60 s)
   ◄── "guardado exitoso"  ◄────────── ID:Guardada ─────────────  GP3 → Z
        (o "error" / "timeout")
```

- Opción `n` en la consola: la operación se **cancela sin consultar al esclavo**.
- Si ninguna bahía está disponible, el flujo termina con el aviso
  `No se encuentran placas disponibles para guardar.`

### 5.4 Flujo de retiro

Idéntico al guardado con dos diferencias: la bahía **no se elige sola** (la indican
el botón GP14/GP15 o el usuario en el menú) y la precondición es estado
`Ocupado`:

```
   GP14/GP15 o menú "2"
        │
        ▼
   ¿la ID está en ESCLAVOS_ACTIVOS? ── No ──► aviso "no detectada" (fin)
        Sí
        ▼
   consulta ID:Disponible ──► ¿"Ocupado"? ── No ──► aviso (fin)
        Sí
        ▼
   menú de consola "Confirme operación (s/n)"
        │ s
        ▼
   envía ID:Retirar (timeout 130 s) ──► proceso_retirado ──► ID:Retirada
                                                              / ID:Disponible
                                                              / ID:Error
        ▼
   informa: "retiro exitoso" / "ya quedó disponible" / "error"
```

### 5.5 Funciones del script maestro

| Función | Rol |
|---|---|
| `limpiar_buffer_uart()` | Descarta bytes residuales antes de cada transmisión |
| `datos_a_texto()` | Conversión segura bytes → texto |
| `leer_respuesta_rs485()` | Espera y **valida** la respuesta (prefijo de ID + contenido permitido según comando) |
| `enviar_consulta()` | Secuencia DE/RE + transmisión + recepción con timeout configurable |
| `respuesta_presente_valida()` | Valida el formato exacto `ID:Presente` del descubrimiento |
| `descubrir_broadcast()` | Emite `*:Descubrir` y colecta respuestas durante su ventana |
| `sondeo_secuencial()` | Respaldo: `Estado` a los IDs 1..4 no hallados |
| `descubrir_esclavos()` | Orquesta las dos fases y devuelve la lista ordenada |
| `buscar_placa_disponible()` | Recorre `ESCLAVOS_ACTIVOS` buscando la primera `Disponible` (3 intentos/placa) |
| `confirmar_por_consola()` | Menú de confirmación `s/n` para operaciones destructivas |
| `esperar_confirmacion_guardado()` | Confirmación + envío de `Guardar` (130 s) + interpretación de la respuesta |
| `esperar_confirmacion_retiro()` | Confirmación + envío de `Retirar` (130 s) + interpretación de la respuesta |
| `ejecutar_accion_en_placa()` | Camino del menú: valida ID, precondición y dispara la confirmación |
| `seleccionar_*_por_consola()` | Menús de consola (acción y placa) validados contra lo detectado |

### 5.6 Validación de respuestas

Para cada respuesta el maestro exige:

1. Prefijo `"<ID consultada>:"` — lo que evita aceptar respuestas de otra placa o
   tramas ajenas.
2. Contenido según el comando enviado:

| Comando | Contenidos aceptados |
|---|---|
| `Disponible` | `Disponible`, `Ocupado`, `Error`, `RetireSuBicicleta` |
| `Guardar` | `Guardada`, `Error`, `Disponible` |
| `Retirar` | `Retirada`, `Disponible`, `Error` |
| otro (`Estado`, etc.) | cualquiera (solo se exige el prefijo) |

---

## 6. Secuencias temporales de ejemplo

### 6.1 Descubrimiento al arranque (dos esclavos conectados)

```
 t [ms]     MAESTRO                         BUS                ESCLAVOS
   0        emite *:Descubrir  ────────────►  │
  ~50        (ventana abierta 1000 ms)       │   E1 espera ~BASE1+rnd
  ~300                                          ◄── 1:Presente ───
  ~500                                          ◄── 2:Presente ───
 1050        fin de ventana, ronda 2         │
 1100        emite *:Descubrir  ────────────►│
 ~2300       fin ronda 2 + sondeo (si hizo   │
             falta, 400 ms por ID ausente)   │
             ESCLAVOS_ACTIVOS = ["1", "2"]   │
```

### 6.2 Guardado exitoso (caso nominal)

```
 EVENTE                          T [s aprox.]
 pulsar GP13                         0
 respuesta "1:Disponible"          ~0,2
 usuario escribe "s"               acción manual del usuario
 esclavo recibe "1:Guardar"        +0,2
 usuario coloca bici + perno       acción manual (≤ 60 s)
 GP3 → 0  (CIERRA)                 + muestreo 20 ms
 cerrojo reporta cerrado           ~0,1 (actuación del solenoide)
 GP3 → Z ; "1:Guardada"            ~+0,05
 maestro: "guardado exitoso"       +0,1
```

### 6.3 Guardado con abandono (timeout)

```
 esclavo recibe "1:Guardar"
 ... el usuario no hace nada ...
 a los 60 s:  GP3 queda en Z, envía "1:Error"
 maestro: "La placa 1 respondió con error al guardar."
 (si tampoco llegara la respuesta, el maestro agota su
  timeout de 130 s e informa "Timeout de guardado")
```

### 6.4 Retiro exitoso

```
 pulsar GP14 / menú "2"
 precondición "Ocupado" verificada
 usuario confirma "s"
 esclavo recibe "1:Retirar"
 GP4 → 0  (ABRE)                    inmediato (fase 1 se revalida al instante)
 cerrojo reporta abierto (0,0,0,1)  ≤ 60 s
 GP4 → Z ; "1:Retirada"
 usuario extrae bicicleta y perno
```

---

## 7. Tabla resumen de parámetros

*Valores referidos a la versión vigente del código (línea indicada en el archivo).*

### Placa maestra — `Uart_maestro/main.py`

| Parámetro | Valor | Línea | Función |
|---|---|---|---|
| `TIMEOUT_RESPUESTA_MS` | 10000 ms | 18 | Timeout de respuestas a consultas (`Disponible`, `Estado`) |
| `TIMEOUT_OPERACION_MS` | 130000 ms | 19 | Timeout de `Guardar`/`Retirar` (60 s + 60 s de fases + margen) |
| `MAX_INTENTOS` | 3 | 17 | Intentos de consulta por placa en la búsqueda de disponibilidad |
| `RETRY_DELAY_MS` | 1500 ms | 20 | Espera entre reintentos de consulta |
| `MAX_ID_SONDEO` | 4 | 23 | Rango del sondeo secuencial de respaldo (IDs 1..4) |
| `ROUNDS_BROADCAST` | 2 | 24 | Rondas de difusión en el descubrimiento |
| `TIMEOUT_DESCUBRIMIENTO_MS` | 1000 ms | 25 | Ventana de escucha por ronda de descubrimiento |
| `TIMEOUT_SONDEO_MS` | 400 ms | 26 | Timeout del sondeo secuencial |
| UART0 | 9600 baud, timeout 3000 ms | 8 | Enlace serie hacia el transceptor |

### Placas esclavas — `Uart1/main.py` y `Uart2/main.py`

| Parámetro | Valor | Línea | Función |
|---|---|---|---|
| `MI_ID` | `"1"` / `"2"` | 8 | Dirección RS485 de la placa (única diferencia entre ambos archivos) |
| `TIEMPO_ESPERA_MS` | 60000 ms | 26 | Tope de **cada fase** (activación y cierre) en guardado/retiro |
| `TIEMPO_ENTRE_LECTURAS_MS` | 20 ms | 27 | Muestreo de los sensores dentro de las secuencias |
| `RETARDO_BASE_MS` | `unique_id % 600` | 33 | Retardo base anti-colisión por placa (derivado del número de serie) |
| `VENTANA_ALEATORIA_MS` | 150 ms | 34 | Componente aleatoria del retardo de respuesta al broadcast |
| UART0 | 9600 baud, timeout 2000 ms | 11 | Enlace serie hacia el transceptor |

### Convención de tiempos

| Constante de tiempo | Dónde actúa |
|---|---|
| 20 ms / 30 ms / 50–80 ms | Secuencia DE/RE de transmisión (armado, espera de `txdone`, guarda de recepción) |
| 20 ms | Muestreo de sensores en las secuencias; *debounce* de confirmación |
| 100 ms | Pausa entre pasadas del bucle principal (esclavo y maestro) |
| 400 ms | Sondeo secuencial de respaldo del descubrimiento |
| 1000 ms | Ventana de escucha del broadcast de descubrimiento |
| 1,5 s | Espera entre reintentos al buscar bahía disponible |
| 3 s | Espera del reintento de descubrimiento si no hubo resultados |
| 60 s | Tope por fase de guardado/retiro en el esclavo |
| 10 s | Timeout de consultas de estado del maestro |
| 130 s | Timeout de las operaciones `Guardar`/`Retirar` del maestro |

---

## 8. Consideraciones de diseño y limitaciones

1. **El descubrimiento es de arranque.** `ESCLAVOS_ACTIVOS` no se reevalúa durante
   la operación: una placa que se desconecte después del arranque seguirá listada y
   sus consultas agotarán el timeout (10 s). Detectarla de nuevo exige reiniciar el
   maestro. Esto es intencional: mantiene el bus libre y el arranque predecible.
2. **Un esclavo en pleno proceso no escucha.** Durante `proceso_guardado` o
   `proceso_retirado` (hasta 60 + 60 s) la placa no vuelve al bucle de recepción, por
   lo que no respondería a nuevas órdenes ni a un descubrimiento. Como el
   descubrimiento solo ocurre al arranque y cada operación es única, esto no afecta
   el flujo normal.
3. **La consola queda bloqueada durante la operación.** Tras confirmar con `s`, el
   maestro espera hasta 130 s la respuesta del esclavo; en ese lapso no atiende
   menú ni botones (el sistema es monohilo).
4. **Las salidas en Z requieren etapa externa adecuada.** GP3/GP4 en reposo no
   drivean nada; se asume que el driver del solenoide incorpora su propio pull para
   mantenerse inactivo. Si la etapa fuera CMOS pura sin pull, la línea quedaría
   indefinida en reposo.
5. **Semántica de `Error`.** El esclavo responde `ID:Error` cuando: la precondición
   de estado no se cumple, o expira cualquiera de los topes de 60 s. El maestro lo
   traduce a mensajes de consola diferenciados por operación.
6. **Alcance del sondeo secuencial.** El respaldo del descubrimiento cubre IDs 1 a
   4 (`MAX_ID_SONDEO`); una placa configurada con ID mayor solo puede detectarse
   por la vía de broadcast. Para escalar, basta incrementar esa constante.
7. **Colisiones potenciales en el broadcast.** Si dos placas compartieran el mismo
   `RETARDO_BASE_MS` y sus aleatorios cayeran juntos, una respuesta podría perderse;
   la segunda ronda de difusión y el sondeo secuencial existen justamente para
   cubrir ese caso (régimen híbrido).
8. **Robustez de validación.** Todo dato entrante se valida (formato de trama,
   prefijo de ID, contenido según comando) y cualquier excepción UART deja el
   transceptor en modo escucha, de modo que una trama corrupta no desincroniza al
   esclavo ni bloquea el bus.
9. **Bloqueo en botones.** Tras iniciar una operación desde GP13/GP14/GP15, el
   bucle espera a que se suelte el botón antes de volver a evaluar entradas
   (evita reentrancias), por lo que un retomo sostenido no dispara dos operaciones.

---

*Fin del informe. Los valores, patrones de estado y tiempos descriptos corresponden a
`Uart_maestro/main.py`, `Uart1/main.py` y `Uart2/main.py` en la versión vigente del
repositorio; ante cualquier modificación de constantes o de la tabla de estados,
este documento debe actualizarse en consecuencia.*
