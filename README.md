# Vortexs Clips

Saca de tus directos de Twitch las rondas de VALORANT en las que haces **3 kills o más** (3K, 4K y ACE) y genera:

- un vídeo **horizontal** con todas las jugadas,
- un vídeo **vertical 9:16 como tus TikToks** (juego a toda la altura, tu cámara arriba y subtítulos automáticos),
- y, si quieres, **un clip suelto por ronda**.

## Instalación

1. Descarga **VortexsClips-Setup.exe**: https://github.com/manuelferjo/VortexsClips/releases/latest/download/VortexsClips-Setup.exe
2. Ábrelo y sigue los pasos. No pide permisos de administrador y ya trae ffmpeg dentro.
3. Abre **Vortexs Clips** desde el escritorio o desde el menú Inicio.

Se actualiza solo: al abrirlo mira si hay una versión nueva y, si le dices que sí, se instala en silencio y se vuelve a abrir.

> ¿Tenías la versión antigua (la de `Iniciar.bat`)? Al abrirla te ofrecerá pasarte a la instalable. Tu clave, tus ajustes y tu cámara se mantienen.

## Clave de la API (gratis)

Los datos de cada kill vienen de **HenrikDev API**, que da los mismos datos de Riot que usa tracker.gg.

1. Entra en https://api.henrikdev.xyz/dashboard/ e inicia sesión.
2. Abre **API Keys** y crea una clave **Basic** para VALORANT (es gratis).
3. Pégala en **Clave API** (panel 1). Se guarda.

## Uso rápido

1. Pon tu **Riot ID** y la **Clave API**.
2. En **2 · VOD y ajustes › VOD**, pega el enlace del VOD de Twitch (o **varios**, separados por espacios) y pulsa **Descargar VOD**.
3. A partir de ahí el programa lo hace todo solo:
   **1 · Descargar VOD › 2 · Buscar partidas › 3 · Sincronizar › 4 · Sacar rondas › 5 · Generar vídeos**

Si ese VOD ya estaba descargado, no lo vuelve a bajar. Si pegaste varios, los procesa uno detrás de otro.

## Panel 2 · VOD y ajustes

### Pestaña VOD

| Opción | Qué hace |
|---|---|
| **Enlaces + Descargar VOD** | Descarga en la máxima calidad (normalmente 1080p60) y coge de Twitch la hora exacta del directo. Si pegas varios enlaces, se procesan en cola. |
| **Al terminar la descarga…** | Si está marcada, después de la descarga lo procesa todo solo. |
| **Carpeta…** | Dónde se guardan los VODs y los vídeos. |
| **o elegir un VOD del PC…** | Para un vídeo que ya tengas. En ese caso, revisa la hora de **Inicio del directo**. |

### Pestaña Cortes

| Opción | Qué hace |
|---|---|
| **Kills mínimas por ronda** | 3 saca 3K, 4K y ACE; 4, solo 4K y ACE; 5, solo ACE. |
| **Solo las kills** | Desde unos segundos antes de tu primera kill hasta la última, cortando los tiempos muertos entre ellas. Eliges los **segundos antes** y **después** de cada kill; si dos kills están más cerca, se juntan en un solo trozo. |
| **La ronda completa** | Toda la ronda, desde que bajan las barreras (o desde la compra, si desmarcas «Saltar fase de compra»), más el margen que elijas. |
| **Sincronizar solo cada partida** | Busca en el killfeed del VOD el patrón exacto de las kills de cada partida y la deja cuadrada al segundo. |
| **Desfase** | Unos **115 s**: lo que tarda la selección de agente y la carga desde la hora que da Riot. **Se ajusta solo** cuando varias partidas se sincronizan bien con el cartel. Si Vortexs Live registró el delay del directo, ese delay se suma solo; si no hay registro y pusiste delay, súmalo tú. |

### Pestaña Salida

| Opción | Qué hace |
|---|---|
| **Codificar con** | «Automático» usa tu gráfica (NVIDIA, AMD o Intel) si puede, que es mucho más rápido; si no, la CPU. Debajo verás cuál ha detectado. |
| **Calidad** | Misma resolución y fps que el directo, y como mucho el bitrate original, para que no se note pérdida. |
| **Versión vertical 9:16** | Como tus TikToks: el juego recortado al centro a toda la altura y tu cámara encima, centrada arriba. |
| **Elegir cámara…** | Sale un fotograma del VOD: arrastra un recuadro sobre tu cámara y queda guardado. |
| **Subtítulos automáticos** | Transcribe lo que dices y lo pone en la vertical, en grande, blanco con borde negro, justo en el borde de abajo de la cámara. La primera vez se descarga el transcriptor (unos 200 MB). Funciona en tu PC, sin internet. |
| **Un clip suelto por cada ronda** | Además del vídeo unido, una carpeta con un archivo por ronda (y su vertical). |

### Pestaña Automático

| Opción | Qué hace |
|---|---|
| **Tu canal de Twitch** | Por ejemplo, `vortexs`. |
| **Vigilar el canal** | Cada 10 minutos mira tu canal. Cuando termina un directo, descarga el VOD nuevo y genera todos los vídeos sin que hagas nada. Solo procesa los directos que salgan **después** de activarlo. |
| **Abrir al encender el PC** | Vortexs Clips se abre minimizado al encender Windows, para que la vigilancia funcione siempre. |

## Usarlo en varios PCs

- **Instálalo igual** en cada PC (VortexsClips-Setup.exe). Se actualizan todos solos.
- **Pasar tus ajustes** (clave de la API, cámara de la vertical, desfase, carpetas…): en el PC que ya lo tienes, ve a **Automático → Exportar ajustes**; en el otro, **Importar ajustes**. El archivo lleva tu clave de la API: no lo compartas.
- **PC donde streameas:** es el mejor sitio para procesar, porque ahí está el registro de delay de Vortexs Live y la vigilancia del canal funciona al acabar cada directo. Nunca procesa mientras estás en directo.
- **Procesar en otro PC:** en **Cortes → Elegir registro…**, elige una copia de `delay_log.csv` del PC de streaming (por ejemplo, en una carpeta compartida o de Google Drive).

## Panel 3 · Partidas

| Botón | Qué hace |
|---|---|
| **☐ / ☑** | Incluir o quitar una partida. |
| **Marcar las que están en el VOD** | Marca las partidas jugadas durante ese directo. |
| **Sincronizar solo** | Sincroniza las partidas marcadas mirando el killfeed. En «Sincro» verás «Auto ✔» y lo seguro que está. |
| **Sincronizar a mano…** | El VOD se reproduce dentro de la ventana, cerca de tu primera kill. Cuando la veas, pulsa **✔ Usar este momento** y luego **Guardar**. |
| **Quitar sincro** | Vuelve a la hora + delay. |

## Panel 4 · Rondas y vista previa

| Botón | Qué hace |
|---|---|
| **Calcular rondas** | Lista de trozos que se van a cortar, con su minuto en el VOD. |
| **Ver ronda** (o doble clic) | Abre la pestaña **Vista previa** y reproduce ese trozo dentro del programa, con el minuto del VOD arriba. Botones ±10 s y ±1 min; también con las flechas y espacio. |
| **Quitar** | Saca del vídeo los trozos seleccionados. |

## Abajo

| Botón | Qué hace |
|---|---|
| **GENERAR VÍDEO** | Genera todos los vídeos, con imagen y sonido sincronizados. |
| **Cancelar** | Para la descarga, la sincronización o la generación. |
| **Pasos y barra** | En qué paso está, el porcentaje, los GB, la velocidad y el tiempo que queda. |

## Qué sale al final (en la carpeta del VOD)

- `… - rondas 3K+.mp4`: horizontal.
- `… - rondas 3K+ (vertical).mp4`: TikTok, con subtítulos.
- `… - rondas 3K+ - clips sueltos\`: un archivo por ronda, horizontal y vertical.
- `… - rondas 3K+_marcas.txt`: minuto de cada trozo en el VOD y en el vídeo, para tu editor.

## Delay de Vortexs Live

Si Vortexs Live guarda su registro de delay (`%APPDATA%\VortexsLive\delay_log.csv`, una línea `milisegundos_epoch;delay_en_segundos` por cada cambio), Vortexs Clips lo lee solo. En la pestaña Cortes verás «✔ Registro de delay encontrado». Con eso, cada partida usa el delay que había en ese momento exacto, aunque lo cambiaras a mitad de directo.

## Notas

- Los VODs solo para suscriptores no se pueden descargar con el enlace.
- En redes con inspección de conexiones (trabajo), el programa usa los certificados de Windows. Si aun así falla, esa red bloquea Twitch.

## Para el desarrollador

Para publicar una versión, sube `VERSION` en `version.py` y súbelo a `main`. GitHub Actions crea la Release con el zip del código y construye en Windows `VortexsClips-Setup.exe` (PyInstaller + ffmpeg + Inno Setup).
