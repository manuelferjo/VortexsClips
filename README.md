# Vortexs Clips

Saca las rondas de competitivo en las que haces **3 kills o más** (3K, 4K y ACE) y las recorta de tu VOD de Twitch en **un solo vídeo**.

## Instalación (solo la primera vez)

1. Descomprime la carpeta donde quieras.
2. Haz doble clic en **Instalar.bat**. Instala Python y ffmpeg con winget.
3. Abre el programa con **Iniciar.bat**.

## Clave de la API (gratis)

tracker.gg no tiene una API pública para VALORANT, así que el programa usa **HenrikDev API**, que da los mismos datos de Riot: kills de cada ronda con su segundo exacto.

1. Entra en https://api.henrikdev.xyz/dashboard e inicia sesión con Discord.
2. Crea una clave **Basic** (es gratis y te la dan al momento).
3. Pégala en «Clave API». Se guarda para la próxima vez.

## La forma rápida: con el enlace de Twitch

1. La primera vez, pon tu **Clave API**.
2. Pega el enlace del VOD (por ejemplo, `https://www.twitch.tv/videos/1234567890`) en el recuadro de abajo a la derecha.
3. Pulsa **Descargar VOD**.

A partir de ahí el programa lo hace todo solo:

1. Descarga el VOD en la **máxima calidad** que tenga en Twitch (normalmente 1080p60).
2. Copia de Twitch la **hora exacta** en que empezó el directo.
3. Busca las partidas de competitivo que jugaste durante ese directo.
4. Saca las rondas en las que hiciste 3 kills o más.
5. Genera el vídeo junto al VOD, con el nombre `… - rondas 3K+.mp4`.

La primera vez también descarga, él solo, el programa que usa para bajar VODs (yt-dlp), y lo mantiene actualizado.

## Panel de progreso (abajo)

Siempre ves qué está haciendo el programa:

- **Pasos**: `1 · Descargar VOD › 2 · Buscar partidas › 3 · Sacar rondas › 4 · Generar vídeo`. El paso actual sale en amarillo y los terminados en morado.
- **Barra y porcentaje**: lo que lleva hecho del paso actual.
- **Detalles**: en la descarga, los GB descargados de los totales, la velocidad y el tiempo que queda. Al generar el vídeo, los minutos procesados, el tiempo que queda y la velocidad.
- **Tiempo en este paso**: arriba a la derecha.
- Si la barra se mueve de lado a lado, el programa está trabajando en algo que no se puede medir (por ejemplo, preparar el MP4). Solo hay que esperar.

## Qué hace cada botón

### Arriba

| Botón | Qué hace |
|---|---|
| **Buscar actualizaciones** | Mira si hay una versión nueva en GitHub. Si la hay, la instala y reinicia el programa. Esto también lo hace solo cada vez que abres el programa. |

### 1 · Cuenta y partidas

| Botón o casilla | Qué hace |
|---|---|
| **Riot ID** | Tu cuenta, por ejemplo `Vortexs#vrtxs`. |
| **Región** | `eu` para Europa. |
| **Clave API** | La clave gratuita de HenrikDev, que sirve para leer tus partidas. Se guarda. |
| **Cargar partidas** | Trae tus últimas 10 partidas de competitivo. |
| **Cargar 10 más** | Trae las 10 anteriores a las que ya tienes en la lista. |
| **Añadir** | Añade una partida concreta pegando su enlace de tracker.gg. |

### 2 · VOD y ajustes

| Botón o casilla | Qué hace |
|---|---|
| **Elegir VOD…** | Abre un vídeo que ya tengas en el PC. |
| **Inicio del directo** | Fecha y hora en que empezó el directo. Si descargas con el enlace, se rellena sola con la hora exacta. |
| **Delay / desfase (s)** | Segundos de delay que tenía el directo en Vortexs Live. Sirve para que los cortes no caigan antes de tiempo. |
| **Kills mínimas** | 3 saca los 3K, 4K y ACE. 4 saca solo los 4K y ACE. 5, solo los ACE. |
| **Saltar fase de compra** | Si está marcada, cada ronda empieza justo antes de que bajen las barreras. Si no, sale la ronda entera con la compra. |
| **Margen antes / después** | Segundos de más al principio y al final de cada ronda. |
| **Codificar con** | Con qué se genera el vídeo. NVIDIA, Intel o AMD son mucho más rápidos si tienes esa gráfica; la CPU funciona siempre. |
| **Descargar VOD** | Descarga el VOD del enlace pegado y, si está marcada la casilla de abajo, lo procesa entero. |
| **Carpeta…** | Dónde se guardan los VODs descargados y los vídeos. |
| **Al terminar la descarga, sacar las rondas y generar el vídeo** | Si está marcada, después de la descarga lo hace todo solo. Si no, solo descarga y lo carga en el programa. |

### 3 · Partidas

| Botón o casilla | Qué hace |
|---|---|
| **☐ / ☑** | Haz clic para incluir o quitar una partida. |
| **¿En el VOD?** | En amarillo («Sí»), las partidas que se jugaron durante ese directo. |
| **Marcar las que están en el VOD** | Marca automáticamente las partidas que caen dentro del VOD. |
| **Sincronizar partida…** | Para dejar una partida cuadrada al segundo: le dices en qué minuto del VOD aparece tu primera kill y el programa cuadra todas las rondas de esa partida. «Ver ese momento» te enseña ese trozo para comprobarlo. |
| **Quitar sincro** | Vuelve a calcular esa partida con la hora del directo. |

### 4 · Rondas

| Botón | Qué hace |
|---|---|
| **Calcular rondas** | Saca la lista de rondas que se van a cortar, con su minuto en el VOD. |
| **Ver ronda** | Reproduce esa ronda del VOD para comprobar que está bien cortada. También puedes hacer doble clic en la ronda. |
| **Quitar** | Quita de la lista las rondas seleccionadas para que no salgan en el vídeo. |

### Abajo

| Botón | Qué hace |
|---|---|
| **GENERAR VÍDEO** | Corta todas las rondas de la lista y las une en un MP4, con imagen y sonido sincronizados. Junto al vídeo guarda `…_marcas.txt` con el minuto de cada ronda en el VOD y en el vídeo final, para tu editor. |
| **Cancelar** | Para la descarga o la generación del vídeo. |

## Sincronizar una partida (para que quede al segundo)

La hora exacta de Twitch ya deja los cortes muy bien. Aun así, si una partida sale unos segundos desplazada (por ejemplo, si cambió el delay dinámico), usa **Sincronizar partida…**: pon el minuto del VOD en el que sale tu primera kill y todas las rondas de esa partida quedan cuadradas. La sincronización queda guardada.

## Notas

- Los VODs que son solo para suscriptores no se pueden descargar con el enlace. Ponlo público un momento o descárgalo a mano y usa **Elegir VOD…**.
- Si en una red (por ejemplo, la del trabajo) falla la descarga por un «certificado», el programa ya usa los certificados de Windows. Si aun así falla, esa red bloquea Twitch: descárgalo desde casa.
- Imagen y sonido: cada ronda se corta y se une en una sola pasada, así que el audio no se desfasa aunque juntes muchas rondas.

## Actualizaciones

El programa comprueba si hay versión nueva cada vez que lo abres, y también puedes buscarla con el botón «Buscar actualizaciones». Si hay una nueva, te pregunta, la descarga y se reinicia solo. Tu clave y tus sincronizaciones no se tocan, porque se guardan en `%APPDATA%\VortexsClips`.

### Publicar una versión nueva (para el desarrollador)

1. Cambia `VERSION` en `version.py` (por ejemplo, `1.0.1`).
2. Sube el cambio a `main`.
3. GitHub Actions crea la Release `v1.0.1` con `VortexsClips.zip`, y los programas instalados se actualizan al abrirse.
