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

## Cómo se usa

1. **Cargar partidas**: descarga tus últimas 10 de competitivo (Vortexs#vrtxs, región `eu`). «Cargar 10 más» trae las anteriores. También puedes pegar el enlace de una partida de tracker.gg (`tracker.gg/valorant/match/…`).
2. **Elegir VOD**: elige el vídeo descargado. Revisa la **hora de inicio del directo**: el programa la intenta adivinar, pero lo exacto es copiarla del panel de Twitch.
3. **Delay / desfase**: si el directo tenía delay en Vortexs Live, pon aquí los segundos (por ejemplo, `30`).
4. Pulsa **«Marcar las que están en el VOD»** y después **«Calcular rondas»**.
5. Haz doble clic en una ronda para comprobarla. Si una partida sale desplazada, usa **«Sincronizar partida…»** (lo explico abajo).
6. Pulsa **GENERAR VÍDEO**. Junto al vídeo se guarda `…_marcas.txt`, con el minuto de cada ronda en el VOD y en el vídeo final, para tu editor.

## Sincronizar una partida (para que quede al segundo)

Con la hora del directo, los cortes pueden caer unos segundos antes o después (por la carga de la partida o por el delay dinámico). Para dejarla exacta:

- Selecciona la partida y pulsa **«Sincronizar partida…»**.
- El programa te dice cuál fue tu **primera kill**: la ronda y el arma.
- Busca ese momento en el VOD y escribe cuándo aparece en el killfeed (por ejemplo, `1:23:45`). Con «Ver ese momento» puedes comprobarlo.
- A partir de ahí, todas las rondas de esa partida se cortan cuadradas. La sincronización queda guardada.

Si el delay dinámico cambió **durante** una partida, puede haber una ronda descuadrada. Quítala con «Quitar» o ajústala a mano.

## Ajustes

| Ajuste | Qué hace |
|---|---|
| Kills mínimas | 3 de serie. Pon 4 para quedarte solo con 4K y ACE. |
| Saltar fase de compra | Empieza el clip 3 s antes de que bajen las barreras. Si lo desmarcas, sale la ronda entera con la compra. |
| Margen antes / después | Segundos de colchón al principio y al final de cada ronda. |
| Codificar con | NVIDIA, Intel o AMD van mucho más rápido que la CPU si tienes esa gráfica. |
| Rótulo | Muestra «4K · Ascent · Ronda 7» en amarillo durante los primeros 4 segundos de cada ronda. |

## Actualizaciones

El programa comprueba si hay versión nueva cada vez que lo abres, y también puedes buscarla con el botón «Buscar actualizaciones». Si hay una nueva, te pregunta, la descarga y se reinicia solo. Tu clave y tus sincronizaciones no se tocan, porque se guardan en `%APPDATA%\VortexsClips`.

### Publicar una versión nueva (para el desarrollador)

1. Cambia `VERSION` en `version.py` (por ejemplo, `1.0.1`).
2. Sube el cambio a `main`.
3. GitHub Actions crea la Release `v1.0.1` con `VortexsClips.zip`, y los programas instalados se actualizan al abrirse.
