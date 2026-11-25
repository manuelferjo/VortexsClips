# Vortexs Clips

Saca de tus directos de Twitch tus mejores jugadas de VALORANT (**3K, 4K, ACE y clutches**) y te las da listas para editar y subir:

- un vídeo **horizontal** con todas las jugadas del directo,
- un vídeo **vertical 9:16 como tus TikToks** (juego a toda la altura y tu cámara arriba), con **subtítulos automáticos**,
- **un clip por jugada**, ordenado en carpetas (3K, 4K, ACE, Clutch y TOP),
- y un archivo con el minuto de cada jugada para tu editor.

La interfaz sigue el estilo de Vortexs Live: botones, interruptores y pestañas con animaciones suaves, brillos de la marca en la cabecera y en el panel de progreso.

No «mira» el vídeo para adivinar las jugadas: usa los datos oficiales de cada partida (el segundo exacto de cada kill) y los cuadra con el VOD.

---

## 1. Instalación

1. Descarga el instalador: https://github.com/manuelferjo/VortexsClips/releases/latest/download/VortexsClips-Setup.exe
2. Ábrelo y sigue los pasos. No pide permisos de administrador y ya trae todo lo necesario (ffmpeg y el transcriptor de voz).
3. Abre **Vortexs Clips** desde el escritorio o el menú Inicio.

**Actualizaciones:** al abrirlo, mira si hay versión nueva; si le dices que sí, se instala sola y se vuelve a abrir. Si está trabajando, espera a que termine para no cortar nada. También tienes el botón **Buscar actualizaciones** (arriba a la derecha).

---

## 2. La primera vez (5 minutos)

1. **Clave de la API (gratis).** Las partidas se leen de HenrikDev API (los mismos datos de Riot que usa tracker.gg).
   Entra en https://api.henrikdev.xyz/dashboard/ → **API Keys** → crea una clave **Basic** de VALORANT → pégala en **Clave API**.
2. **Riot ID** de la cuenta con la que juegas (por ejemplo, `Vortexs#vrtxs`) y **Región** `eu`.
3. **Tu cámara para la vertical:** pestaña **Salida → Elegir cámara…**, arrastra un recuadro sobre tu cámara en el fotograma que sale y pulsa **Guardar**. Solo una vez.
4. **(Opcional) Que lo haga todo solo:** pestaña **Automático** → escribe tu canal de Twitch y marca **Vigilar el canal** y **Abrir al encender el PC**.

Todo queda guardado.

---

## 3. Uso diario

### Opción A · Automática (recomendada, en el PC donde streameas)
Con **Vigilar el canal** activado, no tienes que hacer nada: cuando acaba el directo, Vortexs Clips descarga el VOD y genera todos los vídeos. Nunca trabaja mientras estás en directo.

### Opción B · Con el enlace del VOD
1. Pestaña **VOD** → pega el enlace (`https://www.twitch.tv/videos/…`). Puedes pegar varios y se hacen en cola.
2. Deja marcada **Al terminar la descarga, sacar las rondas y generar los vídeos**.
3. Pulsa **Descargar VOD**. Si ese VOD ya está en tu carpeta, no lo vuelve a bajar.

### Opción C · Con un vídeo que ya tienes en el PC
**o elegir un VOD del PC…** y revisa **Inicio del directo** (con el enlace de Twitch se pone sola; con un archivo, no siempre).

### Lo que hace por dentro (lo ves abajo, en el panel de progreso)
`1 · Descargar VOD › 2 · Buscar partidas › 3 · Sincronizar › 4 · Sacar rondas › 5 · Generar vídeos`

---

## 4. Qué hace cada cosa

### Tarjeta 1 · Cuenta y partidas
| Botón | Qué hace |
|---|---|
| **Cargar partidas** | Trae tus partidas de competitivo. Si hay un VOD cargado, va hacia atrás sola hasta el día de ese directo. |
| **Cargar 10 más** | Trae las 10 anteriores. |
| **Añadir** | Añade una partida concreta pegando su enlace de tracker.gg. |

Las partidas descargadas se guardan: no se vuelven a pedir y, si la API falla o va lenta, se usan las guardadas.

### Tarjeta 2 · VOD y ajustes

**Pestaña VOD**
| Opción | Qué hace |
|---|---|
| **Enlaces + Descargar VOD** | Descarga en la máxima calidad (normalmente 1080p60) con la hora exacta del directo. Si se corta, reintenta y sigue por donde iba. Antes comprueba que haya espacio en el disco. |
| **Al terminar la descarga…** | Si está marcada, después de descargar lo hace todo solo. |
| **Bajar solo los trozos con partidas** | En vez del directo entero (7 h ≈ 20–25 GB), mira antes qué partidas jugaste y baja solo esos trozos, con margen de sobra para la sincronización. Mucho más rápido y ocupa una fracción. Si algo falla, baja el VOD entero como siempre. Se guarda como `… .partes.json` + la carpeta `… (partidas)`; para abrirlo a mano, elige el `.partes.json`. |
| **Borrar el VOD 5 días después** | Cuando ya se han sacado los vídeos de un VOD, lo borra a los 5 días para liberar espacio. Solo borra VODs que bajó el propio programa (nunca uno elegido a mano) y nunca los vídeos generados. **Antes de borrar comprueba que esos vídeos siguen ahí y se abren**: si falta alguno o está roto, no borra el VOD y te avisa, para que puedas volver a generarlos. |
| **Carpeta…** | Dónde se guardan los VODs y los vídeos. |
| **o elegir un VOD del PC…** | Usar un vídeo que ya tengas. |
| **Inicio del directo** | Hora a la que empezó el directo (se pone sola con el enlace). |

**Pestaña Cortes**
| Opción | Qué hace |
|---|---|
| **Kills mínimas por ronda** | 3 = 3K, 4K y ACE · 4 = solo 4K y ACE · 5 = solo ACE. |
| **Clutches ganados de 1 contra N o más** | Incluye también los clutches que ganas (por defecto desde 1 contra 2). Salen con la etiqueta «1v3», etc. |
| **Solo las kills** | Desde unos segundos antes de tu primera kill hasta la última, quitando el tiempo muerto entre medias. Eliges los **segundos antes** y **después** de cada kill, y **no cortar si entre kills hay menos de X s** (por defecto 4) para que no quede a saltos. En los clutches no corta nada desde que te quedas solo hasta el final. |
| **La ronda completa** | Toda la ronda, desde que bajan las barreras (o desde la compra, si desmarcas **Saltar fase de compra**), con el margen que elijas. |
| **Sincronizar solo cada partida** | Cuadra cada partida con el VOD al segundo (ver apartado 6). |
| **Desfase (s)** | Unos **115 s**: lo que hay entre la hora que da Riot y el momento en que empieza el juego (selección de agente y carga). **Se ajusta solo** al sincronizar. |
| **Elegir registro…** | Usar el registro de delay de Vortexs Live de otro PC (ver apartado 8). |
| **Copiar mi registro a…** | En el PC donde streameas: copia el registro de delay a una carpeta compartida (Google Drive). |

**Pestaña Salida**
| Opción | Qué hace |
|---|---|
| **Codificar con** | **Automático** usa tu tarjeta gráfica si puede (mucho más rápido); si no, el procesador. Debajo pone cuál ha detectado. |
| **Calidad** | Misma resolución y fps que el directo, sin pasar de su bitrate: no se nota pérdida. |
| **Versión vertical 9:16** | Como tus TikToks: el juego recortado al centro a toda la altura y tu cámara encima, centrada arriba. |
| **Elegir cámara…** | Marcar dónde está tu cámara en el directo (o **Sin cámara** para solo el juego). |
| **Subtítulos automáticos** | Transcribe lo que dices y lo pone en la vertical, en grande, bajo la cámara. Funciona en tu PC, sin internet (la primera vez descarga el modelo de voz, unos 200 MB). |
| **Palabras destacadas en amarillo y morado** | Algunas palabras salen con los colores del canal. |
| **Revisar y editar los subtítulos viendo el vídeo generado** | Al terminar, abre el editor con el vídeo dentro para corregir palabras, colores o quitarlos; al guardar se rehace solo la vertical. |
| **Un clip suelto por cada ronda** | Además del vídeo unido, una carpeta con un archivo por jugada, ordenados en 3K / 4K / ACE / Clutch. |

**Pestaña Automático**
| Opción | Qué hace |
|---|---|
| **Tu canal de Twitch** | Por ejemplo, `vortexs`. |
| **Vigilar el canal** | Cada 10 minutos mira tu canal; cuando acaba un directo, lo procesa solo. Solo los directos **nuevos** desde que lo activas. |
| **Abrir al encender el PC** | Se abre minimizado al encender Windows, para que la vigilancia funcione siempre. |
| **Exportar / Importar ajustes** | Pasar tus ajustes (clave, cámara, desfase, carpetas…) a otro PC con un archivo. Lleva tu clave de la API: no lo compartas. |

### Tarjeta 3 · Partidas
Arriba verás un **resumen**: partidas dentro del VOD, cuántas están sincronizadas y cuántas jugadas hay para cortar.

| Botón | Qué hace |
|---|---|
| **☐ / ☑** | Incluir o quitar una partida. |
| **Marcar las que están en el VOD** | Marca solas las partidas jugadas durante ese directo. |
| **Sincronizar solo** | Sincroniza las partidas marcadas (varias a la vez). Repetirlo en el mismo VOD es casi instantáneo, porque guarda lo leído. |
| **Sincronizar a mano…** | El VOD se reproduce dentro del programa cerca de tu primera kill: cuando la veas, **✔ Usar este momento** → **Guardar**. Cada vez que corriges a mano, el programa aprende y se ajusta mejor a tu emisión. |
| **Revisar sincro** | Para cada kill tuya enseña dos imágenes del killfeed: **«antes»** (1 s antes) y **«después»** (1,5 s después). Si la partida está bien, la kill aparece en «después» y no en «antes». |
| **Quitar sincro** | Vuelve a la hora + desfase. |
| **Informes** | Abre la carpeta con el registro de la sincronización y los informes de las partidas que no salieron (sirven para mejorar el programa). |

**Columna «Sincro» (con colores):**
| Estado | Qué significa |
|---|---|
| **Auto ✔ (%)** | Sincronizada sola y comprobada. Segura. |
| **Manual ✔** | La sincronizaste tú. Segura. |
| **Auto ≈ (aprox.)** | Ajustada con el desfase del resto del directo, sin señal propia clara. Revísala. |
| **⚠ Dudosa (revísala)** | Al comprobarla con todas sus kills no cuadra; pone cuánto parece desplazada. Revísala con **Revisar sincro** o hazla a mano. |
| **✖ No se encontró** | No se pudo sincronizar. Hazla a mano. |
| **Por hora** | Sin sincronizar: usa la hora + el desfase (en «solo kills» deja margen extra para no perder la jugada). |

### Tarjeta 4 · Rondas y vista previa

La vista previa se ajusta al espacio que haya. **Ampliar ↗** la abre en una ventana grande que puedes agrandar o maximizar. Si la ventana del programa es pequeña, las tarjetas se colocan una debajo de otra y aparece una barra para desplazarte.

| Botón | Qué hace |
|---|---|
| **Calcular rondas** | Lista de jugadas que se van a cortar, con su minuto en el VOD. |
| **Ver ronda** (o doble clic) | La reproduce en la pestaña **Vista previa**, dentro del programa, con el minuto del VOD arriba. Botones ±10 s y ±1 min (también flechas y espacio). |
| **Quitar** | Saca del vídeo las jugadas seleccionadas. |

### Abajo
| | Qué hace |
|---|---|
| **GENERAR VÍDEO** | Genera todo. Va por tandas: si se corta, al repetir reaprovecha lo ya hecho. |
| **Cancelar** | Para lo que esté haciendo. |
| **Corregir subtítulos…** | Reabre el último vídeo generado en el editor de subtítulos (ver apartado 7). |
| **Panel de progreso** | El paso en el que está, el porcentaje, los GB, la velocidad y el tiempo que queda. |

Mientras trabaja, el PC no se suspende (la pantalla sí puede apagarse o bloquearse: no pasa nada).

---

**Top de la semana:** en la pestaña **Automático**, «Top de la semana cada lunes» junta las 10 mejores jugadas (por nota) de los directos de la semana pasada en un vídeo con cuenta atrás (#10 … #1), en horizontal y en vertical, con `publicar.txt` (título, capítulos y hashtags). «Hacer el top ahora…» lo hace al momento.

**Cortes al segundo de la imagen:** al sincronizar, el programa mide cuándo se VEN las kills en tu directo (killfeed y aviso de kill) respecto a la hora de Riot, con la mediana de todas las partidas del VOD, y corta desde ahí. En tus directos la kill se ve ~0,25 s antes que la hora de Riot. Así «Segundos antes de cada kill» y «y después» son exactos sobre la imagen. Si aun así quieres mover todos los cortes, usa **Ajuste fino de todos los cortes** (− = antes, + = después).

## 5. Qué sale al final (una carpeta por directo, junto al VOD)

Todo lo de un directo queda junto en **una carpeta** `2026-11-21 Twitch … - rondas 3K+`:

```
2026-11-21 Twitch … - rondas 3K+\
├─ 2026-11-21 Twitch … - rondas 3K+.mp4              ← vídeo completo en horizontal (YouTube)
├─ 2026-11-21 Twitch … - rondas 3K+ (vertical).mp4   ← vídeo completo en 9:16 con subtítulos
├─ 2026-11-21 Twitch … - rondas 3K+ - publicar.txt   ← título, capítulos y hashtags del vídeo completo
├─ 2026-11-21 Twitch … - rondas 3K+_marcas.txt       ← minuto de cada jugada (para tu editor)
├─ 3K\  4K\  ACE\  Clutch\                            ← un clip por jugada (horizontal y vertical) + su .txt
├─ TOP\                                               ← las 3 mejores del directo
├─ ranking.txt                                        ← todas las jugadas por nota
└─ publicar.txt / publicar.json                       ← textos de cada clip, el mejor primero
```

Al generar a mano, el programa propone esa misma carpeta. Si eliges otra, los clips van en una subcarpeta `… - clips sueltos` al lado del vídeo, para no desordenarla. Los vídeos de versiones anteriores se quedan donde estaban.

**Rápido y sin perder calidad:** la horizontal y la vertical salen en **una sola lectura del VOD**, cada una codificada una única vez desde el original. Los clips sueltos se cortan de esos vídeos **copiando, sin volver a codificar** (tardan segundos y son idénticos).

**Los textos para publicar** se hacen solos con la jugada (3K, 4K, ACE, clutch), el agente, el mapa y, si hay subtítulos, la frase con más «hype» que dijiste. Revísalos antes de subir: son una base.

**La nota de cada jugada** tiene en cuenta si es 3K, 4K, ACE o clutch, si las kills fueron rápidas y el pico de volumen de tu reacción. Al terminar, el aviso final te dice las 3 mejores.

---

## 6. Cómo se sincroniza (explicado fácil)

Riot da el segundo exacto de cada kill, pero en una hora que no coincide exactamente con el VOD: hay unos **115 s** de selección de agente y carga, más el **delay** si lo llevabas, más pequeñas diferencias de cada partida. Para cuadrarlo, el programa busca en el vídeo cosas que pasan en momentos que Riot da exactos:

1. **El cartel de fin de ronda (WON/LOST).** En tu emisión salen dos textos seguidos al acabar cada ronda; el programa busca esa pareja en cada final de ronda. Es el método principal.
2. **El marcador del HUD**, que cambia al acabar cada ronda.
3. **El killfeed** (arriba a la derecha), como apoyo.

**Más rápido en VODs largos:**
- **Mientras se descarga** (con «Bajar solo los trozos con partidas»), cada trozo ya bajado se va analizando mientras baja el siguiente. Al terminar la descarga, la sincronización es casi inmediata.
- **Varias partidas a la vez** según tu PC: 2 en un portátil normal y hasta 4–5 en un PC con muchos núcleos o con gráfica NVIDIA.
- **Mientras se sincroniza**, cada partida ya colocada empieza a transcribirse para los subtítulos. Al llegar a «Generar vídeos» suelen estar hechos.

Para ir más rápido, la tarjeta gráfica descodifica el vídeo y solo pasa al programa los 4 fotogramas por segundo que se analizan (la imagen analizada es la misma; si la gráfica no puede, se hace como siempre).

Si dos métodos coinciden en el mismo segundo, la partida se da por buena. Después **se comprueba con todas sus kills**: si algo no cuadra, sale como «⚠ Dudosa» y te avisa también al generar.

**Con Vortexs Live 4.24 o posterior**, se guarda el delay real de cada momento del directo (`%APPDATA%\VortexsLive\delay_log.csv`) y Vortexs Clips lo suma solo, aunque cambies el delay en pleno directo. Si el directo se cortó y se reconectó, las partidas afectadas usan el desfase de su vecina.

---

## 7. Subtítulos

- Se generan solo para la versión vertical, con lo que dices en ese trozo.
- Estilo de tus TikToks: blanco con borde negro, en minúsculas y en grande, en el borde de abajo de la cámara, una palabra cada vez.
- **Revisar viendo el vídeo:** con **Revisar y editar los subtítulos** marcada, al terminar se abre el editor con el vídeo ya generado dentro. Clic en una palabra y el vídeo salta ahí; **▶ Escuchar desde aquí** lo reproduce con sonido, y el subtítulo se pinta encima tal cual quedará, **cada cambio se ve al momento**. Puedes corregir una palabra (doble clic), cambiar su color (amarillo, morado, sin color o automático), borrarla o quitar todos. **Varias a la vez:** arrastra con el ratón por la lista (o Ctrl/Mayús+clic), o usa **Toda la frase** / **Todo (Ctrl+A)**; luego cambia el color, bórralas o escribe un texto para **juntarlas en una**. Clic derecho abre un menú, y hay teclas rápidas: Supr borrar · A amarillo · M morado · S sin color · Z automático. Al pulsar **Guardar y rehacer la vertical** se rehace solo la vertical (desde el VOD, sin perder calidad) y sus clips. Si no cambias nada, no se rehace nada.
- ¿Lo ves más tarde? Botón **Corregir subtítulos…** (abajo): reabre el último vídeo.
- **Subir a Google Drive** (botón abajo, **Subir a Drive**): sube la carpeta del último vídeo generado tal cual (vídeo completo, vertical, clips en 3K/4K/ACE/TOP, textos…) a la carpeta compartida de Drive, dentro de **Vortexs Clips / dd-mm-aaaa** (la fecha del directo). Nunca se sube solo: siempre pide confirmación. La primera vez, **Automático → Conectar con Google Drive** abre el navegador para iniciar sesión en Google (el programa no ve tu contraseña; usa rclone, que se descarga solo). El enlace de la carpeta y la subcarpeta se cambian en la pestaña Automático.
- **Subtítulos para CapCut** (Salida): la vertical y sus clips salen **sin subtítulos pegados** y al lado de cada uno va:
  - `… (vertical).srt`: los subtítulos palabra a palabra, con las palabras clave marcadas en amarillo y morado;
  - `… (vertical) - sin colores.srt`: lo mismo sin colores, por si CapCut enseña las marcas de color como texto;
  - `… (vertical) - palabras en color.txt`: la lista de palabras clave con su momento y su color, para colorearlas a mano si hace falta.

  En CapCut para PC: abre la vertical, **Subtítulos → Añadir subtítulos → Importar archivo** y elige el `.srt`. Quedan editables ahí: texto, fuente, tamaño, color y animación. Con esta opción no se abre el editor de subtítulos del programa.
- **Killfeed de tus kills en la vertical** (Salida, activado): en cada kill tuya se recorta del directo la línea del killfeed tal cual (arma, headshot, wallbang, habilidad, agentes y nombres) y se queda fija a la derecha, bajo la cámara; las siguientes de la misma ronda se apilan debajo. Cada una entra con un fade rápido y un pequeño destello (solo la línea, no el vídeo). Solo coge **tus** líneas (las del marcador amarillo de «Me»), aunque haya muchas en pantalla o salgan más abajo, y cada una a su largo real.
- **Palabra a palabra:** en la vertical sale una sola palabra cada vez (estilo TikTok). Si juntas varias en una en el editor, salen juntas.
- **Ajuste de tiempo:** en el editor, **◀ −0,1 s / +0,1 s ▶** mueve todos los subtítulos y lo ves al momento en el vídeo. Marca **Usar este ajuste en los próximos vídeos** para guardarlo. También está en **Salida → Ajuste de tiempo (s)** (+ salen más tarde, − antes).
- **Diccionario:** cada palabra que corriges se apunta sola (p. ej. «vortex» → «Vortexs», «tres kills» → «3K») y en los siguientes vídeos ya sale corregida. Se ve y se edita en **Salida → Diccionario…**.
- **Con gráfica NVIDIA** (el PC de stream), la primera vez que abres el programa se baja sola en segundo plano la versión del transcriptor para tu gráfica (unos 440 MB, una sola vez) y los subtítulos salen varias veces más rápido. Si tu gráfica o su driver no la pueden usar, sigue con el procesador como siempre. En un PC sin NVIDIA (el portátil) no se baja nada.
- **Tiempos:** cada palabra se alinea con la voz con precisión (alineación DTW del transcriptor), sin que los tiempos se vayan desplazando en audios largos, y las palabras que el transcriptor «estiraba» tras una pausa (el texto salía antes de decirlo) se corrigen solas.

---

## 8. Usarlo en varios PCs

- Instálalo igual en cada PC; todos se actualizan solos.
- Pasa tus ajustes con **Automático → Exportar ajustes / Importar ajustes**.
- **Lo más sencillo es procesar en el PC donde streameas**: ahí está el registro de delay de Vortexs Live.
- Para procesar en otro PC: en el de streaming, **Cortes → Copiar mi registro a…** tu carpeta de Google Drive (con Google Drive para ordenador instalado); en el otro, **Cortes → Elegir registro…** y elige ese `delay_log.csv`. Si en ese PC no puedes instalar Drive, descarga el archivo desde la web de Drive y elígelo igual.

---

## 9. Problemas frecuentes

| Lo que ves | Qué hacer |
|---|---|
| **Una partida sale «Dudosa», «≈ aprox.» o «No se encontró»** | **Revisar sincro** para verla, o **Sincronizar a mano…** (un minuto). El programa aprende de esa corrección. |
| **Cortes en los que no salen las kills** | Revisa la columna Sincro de esa partida y corrígela a mano. Con Vortexs Live 4.24 debería pasar muy poco. |
| **Sale solo una partida de un directo antiguo** | Pulsa **Cargar partidas** con el VOD ya cargado (va hacia atrás sola) o **Cargar 10 más**. |
| **«Subtítulos: HTTP 403»** | Tu red bloquea la descarga del modelo de voz. El programa lo intenta primero desde GitHub; si aun así falla, hazlo una vez desde otra red (en casa). |
| **Error de «certificado» al descargar el VOD** | Pasa en redes de trabajo que revisan las conexiones. El programa usa los certificados de Windows; si sigue fallando, esa red bloquea Twitch: descárgalo en casa. |
| **El VOD es solo para suscriptores** | No se puede descargar con el enlace: ponlo público un momento o descárgalo a mano y usa **o elegir un VOD del PC…**. |
| **Falta espacio** | El programa avisa antes de empezar. Un VOD de 7 h ocupa unos 20–25 GB: borra los que ya hayas procesado. |
| **La API de partidas no responde** | Reintenta sola y, si no, usa las partidas que ya tenía guardadas. |

Si algo falla, pulsa **Informes** y pasa la carpeta (o el `sincronizacion.txt`) a quien te ayude a revisarlo.

---

## Para el desarrollador

- Código en Python (tkinter, numpy) con ffmpeg, yt-dlp y whisper.cpp. Datos de partidas: HenrikDev API.
- **Publicar una versión:** sube `VERSION` en `version.py` y súbelo a `main`. GitHub Actions construye en Windows el instalador (PyInstaller + Inno Setup, con ffmpeg y whisper.cpp), ejecuta la **autoprueba** del programa empaquetado (ventana, vista previa, vídeo vertical con subtítulos, horizontal + vertical en una pasada, clips por copia, descarga por tramos, textos para publicar, editor de subtítulos con vídeo, sincronización por cartel, transcripción con comprobación de tiempos…) y, solo si pasa, crea la Release con `VortexsClips-Setup.exe` y el zip del código. Los programas instalados se actualizan solos.
- El modelo de voz está en la Release `modelo-voz` (para redes donde HuggingFace está bloqueado).
- `pruebas/`: prueba con VODs y partidas reales en GitHub (no publica nada).
