---
name: Tambor · Libro de operaciones
description: Consola de la API del orquestador multi-agente de Tambor, dibujada como el libro de operaciones de un back office de cobros.
colors:
  sello: "#4b3b8f"
  sello-hondo: "#3a2d70"
  papel: "#edf1e6"
  papel-2: "#e2e8da"
  hoja: "#f8faf4"
  tinta: "#1c2420"
  tinta-2: "#4a5650"
  tinta-3: "#5e6963"
  renglon: "#b9cbd8"
  renglon-fuerte: "#7f9db4"
  borde-control: "#6d8aa1"
  regla: "#c4524b"
  azul: "#2c5a85"
  verde: "#2d6a4a"
  rojo: "#b2332c"
  gris: "#5c655f"
typography:
  display:
    fontFamily: "Archivo, ui-sans-serif, system-ui, sans-serif"
    fontSize: "48px"
    fontWeight: 760
    lineHeight: 1
    letterSpacing: "-0.01em"
    fontVariation: "'wdth' 72"
    fontFeature: "'tnum' 1"
  headline:
    fontFamily: "Archivo, ui-sans-serif, system-ui, sans-serif"
    fontSize: "22px"
    fontWeight: 750
    lineHeight: 1.55
    letterSpacing: "-0.015em"
  title:
    fontFamily: "Archivo, ui-sans-serif, system-ui, sans-serif"
    fontSize: "17px"
    fontWeight: 700
    lineHeight: 1.55
    letterSpacing: "-0.01em"
  body:
    fontFamily: "Archivo, ui-sans-serif, system-ui, sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.55
    fontVariation: "'wdth' 100"
  label:
    fontFamily: "Archivo, ui-sans-serif, system-ui, sans-serif"
    fontSize: "13px"
    fontWeight: 600
    lineHeight: 1.55
  label-condensed:
    fontFamily: "Archivo, ui-sans-serif, system-ui, sans-serif"
    fontSize: "12.5px"
    fontWeight: 700
    lineHeight: 1.55
    fontVariation: "'wdth' 80"
  stamp:
    fontFamily: "Archivo, ui-sans-serif, system-ui, sans-serif"
    fontSize: "10.5px"
    fontWeight: 700
    lineHeight: 1.55
    letterSpacing: "0.06em"
    fontVariation: "'wdth' 85"
  mono:
    fontFamily: "JetBrains Mono, ui-monospace, Cascadia Mono, monospace"
    fontSize: "12.5px"
    fontWeight: 400
    lineHeight: 1.5
    fontFeature: "'tnum' 1"
rounded:
  none: "0px"
spacing:
  r1: "4px"
  r2: "8px"
  r3: "12px"
  r4: "16px"
  r5: "24px"
  r6: "32px"
  r7: "48px"
  renglon: "28px"
components:
  button-sello:
    backgroundColor: "{colors.sello}"
    textColor: "{colors.hoja}"
    rounded: "{rounded.none}"
    padding: "0 16px"
    height: "40px"
  button-sello-hover:
    backgroundColor: "{colors.sello-hondo}"
    textColor: "{colors.hoja}"
  button-rojo:
    backgroundColor: "transparent"
    textColor: "{colors.rojo}"
    rounded: "{rounded.none}"
    padding: "0 16px"
    height: "40px"
  button-texto:
    backgroundColor: "transparent"
    textColor: "{colors.tinta-3}"
    padding: "0"
  saltar:
    backgroundColor: "{colors.sello}"
    textColor: "{colors.hoja}"
    padding: "8px 12px"
  input-campo:
    backgroundColor: "{colors.hoja}"
    textColor: "{colors.tinta}"
    rounded: "{rounded.none}"
    padding: "0 12px"
    height: "40px"
  textarea-boleta:
    backgroundColor: "{colors.hoja}"
    textColor: "{colors.tinta}"
    rounded: "{rounded.none}"
    padding: "0 12px"
  chip-ejemplo:
    backgroundColor: "transparent"
    textColor: "{colors.tinta-2}"
    rounded: "{rounded.none}"
    padding: "3px 8px"
  chip-ejemplo-firma:
    backgroundColor: "transparent"
    textColor: "{colors.sello}"
  sello-en-cola:
    backgroundColor: "transparent"
    textColor: "{colors.gris}"
    typography: "{typography.stamp}"
    rounded: "{rounded.none}"
    padding: "2px 7px"
  sello-en-curso:
    backgroundColor: "transparent"
    textColor: "{colors.azul}"
    typography: "{typography.stamp}"
    padding: "2px 7px"
  sello-espera-firma:
    backgroundColor: "{colors.sello}"
    textColor: "{colors.hoja}"
    typography: "{typography.stamp}"
    padding: "2px 7px"
  sello-resuelto:
    backgroundColor: "transparent"
    textColor: "{colors.verde}"
    typography: "{typography.stamp}"
    padding: "2px 7px"
  sello-fallo:
    backgroundColor: "transparent"
    textColor: "{colors.rojo}"
    typography: "{typography.stamp}"
    padding: "2px 7px"
  fila-libro:
    backgroundColor: "transparent"
    textColor: "{colors.tinta}"
    padding: "8px 24px"
    height: "52px"
  fila-libro-actual:
    backgroundColor: "{colors.hoja}"
  comprobante:
    backgroundColor: "{colors.hoja}"
    textColor: "{colors.tinta}"
    rounded: "{rounded.none}"
    padding: "32px"
    width: "880px"
  firma:
    backgroundColor: "transparent"
    textColor: "{colors.tinta}"
    rounded: "{rounded.none}"
    padding: "24px"
  barra:
    backgroundColor: "{colors.papel-2}"
    textColor: "{colors.tinta}"
    padding: "0 24px"
    height: "56px"
  aviso-toast:
    backgroundColor: "{colors.tinta}"
    textColor: "{colors.hoja}"
    padding: "12px 16px"
    width: "360px"
---

# Design System: Tambor · Libro de operaciones

## Overview

**Creative North Star: "El libro de operaciones y el sello"**

La consola es el libro de un back office de cobros. Cada pedido a la API es un comprobante numerado que se asienta en un libro de renglones azules y recibe sellos de goma a medida que cambia de estado. El papel es contable, verde pálido; la tinta es verde-negra; y hay una sola tinta de acento, el violeta de los sellos, que marca lo que pide una mano humana: encolar, firmar, decidir.

La densidad es la de una planilla de oficina, no la de un panel de marketing: líneas de 1px en lugar de tarjetas, números en monoespaciada tabular, encabezados en Archivo condensado. La jerarquía la dan el ancho de la letra, el peso y las reglas (renglón azul, doble regla roja de margen), no los fondos de color ni las sombras. La única hoja que se levanta del papel es el comprobante abierto, y lleva su propio margen rojo doble como cualquier hoja de libro.

Rechazos confirmados por el contrato de dirección: el panel SaaS de tarjetas redondeadas y el chat de burbujas. La respuesta del sistema se lee como un asiento, no como un mensaje.

**Key Characteristics:**
- Papel verde pálido en tres capas (papel, papel-2, hoja) y tinta verde-negra en tres intensidades.
- Una sola tinta de acento: violeta de sello, reservada a la acción y a la firma.
- Estados como sellos de goma de doble borde, cada uno con su tinta, nunca como badges redondeados.
- Cero radios en toda superficie y control.
- Archivo variable en anchos condensados para rótulos; JetBrains Mono tabular para todo lo que es número o ID.
- Doble regla roja: horizontal entre la cabeza y el cuerpo de cada documento, vertical como margen del libro y del comprobante.

## Colors

Papel contable y tintas de oficina: neutros verdosos de baja croma, renglones azul pizarra, una regla roja y un violeta de sello como única voz de acento.

### Primary
- **Violeta de sello** (#4b3b8f): botón primario (Encolar, Aprobar y firmar), borde doble del bloque de firma y de los cobros a firmar, sello "Espera firma" (el único sello relleno), anillo de foco, cursor de texto, selección, enlace "saltar a la nueva orden", número del asiento abierto y el chip de ejemplo que dispara un reembolso.
- **Violeta hondo** (#3a2d70): hover del botón de sello y del chip de firma, y título del bloque de firma. Nunca aparece solo, siempre como variante del violeta.

### Neutral
- **Papel** (#edf1e6): fondo de la página, la hoja contable.
- **Papel de libro** (#e2e8da): segunda capa, la barra superior y el brillo del esqueleto de carga.
- **Hoja de comprobante** (#f8faf4): superficie de lo que se escribe o se lee de cerca: el comprobante, los campos, la fila activa del libro, las etiquetas de cobro dentro de la firma.
- **Tinta** (#1c2420): texto principal, verde-negro. También el rótulo del supervisor en el recorrido y el fondo del aviso.
- **Tinta segunda** (#4a5650): texto secundario, etiquetas de campo, rótulos de columna, datos del comprobante (6.9:1 sobre papel).
- **Tinta gastada** (#5e6963): ayudas, placeholders, metadatos de fila, horas del historial, contador (5:1 sobre papel).
- **Renglón** (#b9cbd8): líneas entre filas, renglones del área de texto, divisores internos de tablas, pasos y el filete que abre el análisis de la firma.
- **Renglón fuerte** (#7f9db4): bordes estructurales: barra, columna, borde del comprobante, cabeza de listas, barra de scroll.
- **Borde de control** (#6d8aa1): el borde de todo lo que se toca: área de texto, campos y chips de ejemplo. Llega a 3:1 sobre papel, el mínimo para que un control se vea como control; el renglón fuerte no llega y queda para la estructura.
- **Regla roja** (#c4524b): solo como línea, nunca como texto: doble regla de 3px bajo la cabeza de la boleta y del comprobante, el margen doble del libro (al 55% de opacidad) y el margen doble del comprobante (al 70%).

### Tintas de estado
- **Tinta azul** (#2c5a85): estado En curso y enlaces (Phoenix, desplegables "Ver aporte").
- **Tinta verde** (#2d6a4a): estado Resuelto, luz de API en línea, acciones hechas.
- **Tinta roja** (#b2332c): estado Falló, errores de campo, alertas, botón Rechazar, luz de API caída.
- **Tinta gris** (#5c655f): estado En cola y luz de salud mientras conecta.

### Named Rules
**La Regla de la Tinta Única.** El violeta de sello es el único acento. Aparece donde hay una acción o una decisión humana, y en ningún otro lugar: ni como velo de fondo ni como color de rótulo. Si una pantalla nueva necesita "un color para destacar", la respuesta es peso o ancho tipográfico, no otra tinta.

**La Regla de una Tinta por Estado.** Cada estado tiene una tinta fija (gris en cola, azul en curso, violeta espera firma, verde resuelto, rojo falló) y esa tinta colorea borde, doble borde y texto del sello. Solo "Espera firma" va relleno, porque es el único estado que le pide algo a la persona.

**La Regla de la Regla Roja.** El rojo de margen (#c4524b) y el rojo de error (#b2332c) son tintas distintas. La regla es papelería: separa cabeza de cuerpo y marca el margen. El rojo de error es un dato. No se intercambian.

**La Regla del Borde que se Toca.** Lo que se puede tocar lleva borde de control (#6d8aa1, 3:1); lo que solo estructura lleva renglón fuerte. Un control nuevo nunca hereda el borde de una superficie.

## Typography

**Display Font:** Archivo (variable, ancho 62–125, peso 300–800), con ui-sans-serif y system-ui de respaldo
**Body Font:** Archivo, mismo archivo, en ancho 100
**Label/Mono Font:** JetBrains Mono (400 y 600), con ui-monospace y Cascadia Mono de respaldo

**Character:** una grotesca de oficina que se condensa para hablar como encabezado de planilla y se abre para la prosa, junto a una monoespaciada que hace de máquina de numerar. El ancho variable es la herramienta de jerarquía principal, antes que el tamaño.

### Hierarchy
- **Display** (760, 48px, interlínea 1, ancho 72, cifras tabulares): solo el Nº del comprobante abierto. Baja a 34px por debajo de 960px.
- **Headline** (750, 20–24px, -0.015em): títulos de documento: "Nueva orden" (22px), estado vacío (24px), bloque de firma (20px).
- **Title** (700, 17px, -0.01em): apartados del comprobante (Respuesta, Recorrido, Reembolso).
- **Body** (400, 15px, 1.55): texto corriente. El pedido citado en el comprobante sube a 19px y peso 450 con medida de 62ch (17px por debajo de 600px); la prosa de la respuesta va a 15px, interlínea 1.6 y 72ch.
- **Label** (600, 13px): etiquetas de campo y avisos.
- **Label condensado** (600–750, 11.5–13px, ancho 80): rótulos de columna y de dato ("Asientos", "Duración", nombre del agente en el recorrido).
- **Sello** (700, 10.5px, ancho 85, 0.06em, mayúsculas): texto de los sellos de estado; 15px en el sello grande del comprobante.
- **Mono** (400, 11.5–13px, tabular): números de asiento, IDs de trabajo y de cobro, horas, duraciones, contador de caracteres, herramientas usadas.

Los títulos (de la prosa, del estado vacío y la pregunta de la firma) cortan con `text-wrap: balance`; la prosa y el pedido citado, con `text-wrap: pretty`.

### Named Rules
**La Regla del Número a Máquina.** Todo número o identificador (Nº de asiento, `cob_XXXXX`, horas, duraciones, contadores) va en JetBrains Mono con cifras tabulares. La única excepción es el Nº grande del comprobante, que va en Archivo condensado con `tnum`, porque es el titular del documento.

**La Regla del Ancho Condensado.** Los rótulos se condensan (ancho 72–85); el texto que se lee de corrido nunca. Si algo es una etiqueta de columna o de sello, se angosta; si es una oración, va en ancho 100.

**La Regla del Rótulo en Tinta.** Un rótulo se destaca con peso, no con color: el supervisor se distingue de los otros agentes por ir en tinta plena a 750, no en violeta.

## Layout

Una mesa de dos columnas bajo una barra fija. La barra mide 56px, es sticky y lleva marca, salud de la API y el enlace a Phoenix. La columna izquierda (entre 340 y 420px) es sticky bajo la barra y mide exactamente el alto restante de la ventana: apila la boleta "Nueva orden" y el libro de asientos, y solo el libro scrollea, por su cuenta. Una línea de 1px en renglón fuerte la separa de la hoja derecha, que scrollea con la página y centra el comprobante abierto con un ancho máximo de 880px.

El ritmo de espaciado es de base 4 (4, 8, 12, 16, 24, 32, 48px). Los márgenes de bloque usan 24px en la columna y 32px dentro del comprobante; entre apartados del comprobante, 32px. El área de texto de la boleta trabaja sobre un renglón de 28px: la interlínea y las líneas dibujadas coinciden, y la altura mínima es de cuatro renglones.

Las filas del libro son una grilla de tres pistas (número de 40px, texto flexible, sello) con mínimo de 52px de alto. En la cabeza del comprobante, el sello grande es lo primero en el orden del documento y flota a la derecha; el Nº, el pedido y los datos lo rodean (la cabeza cierra el flotado con `flow-root`), así el sello nunca se pisa con el texto en ningún ancho. Los datos van en una grilla de cuatro columnas de ancho propio.

El aviso (toast) vive al pie de la columna del libro, a 24px del borde izquierdo y del inferior, con 360px de máximo: nunca tapa el sello grande ni la firma, que viven en la hoja.

Responsive:
- **≤960px:** una sola columna; la columna deja de ser sticky y toma su alto natural; el libro se limita a 320px de alto con scroll propio; el comprobante baja a 24px de padding; el Nº a 34px; los datos a dos columnas.
- **≤600px:** márgenes laterales de 16px; se ocultan "Libro de operaciones" y "Trazas en" (queda "Phoenix"); el texto de salud queda solo para lectores de pantalla; el pedido citado baja a 17px y pasa debajo del sello en lugar de rodearlo; el aviso ocupa el ancho entre márgenes de 16px; los pasos del recorrido y el formulario de firma se apilan.

## Elevation & Depth

El sistema es plano: la profundidad se dibuja con líneas (renglón, renglón fuerte, doble regla) y con tres capas tonales de papel. Hay exactamente una superficie levantada, el comprobante abierto, con una sombra baja y difusa que lo despega del papel como una hoja apoyada encima. El aviso tiene su propia sombra porque vive por encima de todo y es transitorio. La firma no se levanta ni se tiñe: va plana sobre la hoja, delimitada solo por su doble borde.

### Shadow Vocabulary
- **Hoja apoyada** (`box-shadow: 0 1px 0 rgba(28, 36, 32, 0.04), 0 12px 28px -18px rgba(44, 70, 58, 0.35)`): solo el comprobante.
- **Aviso flotante** (`box-shadow: 0 10px 24px -12px rgba(28, 36, 32, 0.6)`): solo el toast.
- **Filete de foco** (`box-shadow: 0 0 0 1px #4b3b8f`): campos de texto en foco, sumado al borde violeta; duplica el trazo a 2px sin mover el layout.

### Named Rules
**La Regla de la Hoja Única.** Una sola cosa por pantalla se levanta del papel: el documento que se está leyendo. Filas, campos, boleta, firma y sellos quedan planos y se separan con líneas.

## Shapes

Esquinas a escuadra en todo: superficies, campos, botones, chips, sellos y la firma (radio 0). La única forma redonda del sistema es la luz de salud de la API, un punto de 8px, porque es una luz y no un papel.

La forma característica es el **doble borde de sello**: un borde de 1.5–2.5px más un `outline` de 1–1.5px separado por 1.5–3px, de la misma tinta. Lo usan los sellos de estado, el sello "T" de la marca y el bloque de firma. Los sellos sueltos van girados (−2° los chicos, −4° el grande y la marca); en cualquier serie (el libro, el historial, la leyenda del estado vacío) van derechos.

Las líneas también son forma: 1px para renglones y bordes, doble 3px en la regla roja horizontal, doble de 1px con 1–2px de luz en el margen vertical (el libro a la altura del número; el comprobante a 13px de su borde izquierdo, salvo en el estado vacío), discontinuo 1px para los chips de ejemplo (que se vuelven continuos al pasar el puntero).

### Named Rules
**La Regla del Cero Radio.** Nada de papel tiene esquinas redondeadas. Si un componente nuevo necesita suavidad, se resuelve con espacio o con tinta más clara, nunca con `border-radius`.

**La Regla del Sello Derecho en Serie.** Un sello solo se gira cuando está solo. Varios sellos girados iguales en una lista se leen como patrón decorativo; en el libro, el historial y la leyenda van rectos.

**La Regla de la Tinta Fresca.** El trazo que corre del sello En curso solo aparece donde el estado es el de ahora (el sello grande y el libro). En el historial y en la leyenda los sellos quedan quietos: ya pasaron o son ejemplo.

## Components

### Buttons
Firmes y planos, como un sello que se apoya.
- **Shape:** esquinas rectas (0), alto de 40px, padding horizontal de 16px, Archivo 650 a 14px con 0.01em.
- **Primario (sello):** fondo violeta de sello, texto en hoja, icono de flecha o de sello a la derecha del texto.
- **Hover / Focus:** el fondo pasa a violeta hondo (solo con puntero fino). Foco visible con contorno violeta de 2px separado 2px. Al presionar se achica a 0.97 en 140ms. Ocupado: el texto se desenfoca 1.5px y baja a 60% de opacidad. Deshabilitado: 55% de opacidad.
- **Rechazar (rojo):** fondo transparente, texto rojo y filete interior de 1px rojo; hover con velo rojo al 6%.
- **Botón texto:** sin caja, tinta gastada, subrayado a 3px ("Limpiar libro").
- **Saltar:** el primer foco de la página es el enlace "Ir a la nueva orden", violeta con texto hoja, escondido arriba y que baja a 8px del borde al recibir foco.

### Chips (pedidos de ejemplo)
- **Style:** fondo transparente, borde discontinuo de 1px en borde de control, texto en tinta segunda a 12.5px, padding 3px 8px.
- **State:** hover: borde continuo, texto en tinta, fondo hoja. La variante que lleva a una firma usa borde y texto violeta.

### Cards / Containers
El único contenedor es el **comprobante**.
- **Corner Style:** recto (0).
- **Background:** hoja sobre papel.
- **Shadow Strategy:** la Hoja apoyada (ver Elevation & Depth).
- **Border:** 1px renglón fuerte, más el margen rojo doble vertical al 70%.
- **Internal Padding:** 32px (24px por debajo de 960px).
- **Cabeza:** sello grande flotando arriba a la derecha (primero en el documento), Nº en display, pedido citado, fila de datos en mono e historial de sellos; cierra con la doble regla roja.

### Inputs / Fields
- **Style:** fondo hoja, borde 1px en borde de control, esquinas rectas, 40px de alto, cursor de texto violeta.
- **Área de texto de la boleta:** dibuja renglones de libro (línea de 1px en renglón cada 28px, ligada al scroll del contenido); la interlínea es el renglón.
- **Focus:** borde violeta más filete de 1px violeta; sin contorno del navegador.
- **Error:** borde rojo y mensaje rojo de 13px debajo, anunciado como alerta.

### Navigation
- **Barra:** 56px, sticky, fondo papel de libro, borde inferior 1px renglón fuerte. A la izquierda el sello "T" (26px, doble borde violeta, girado −4°), "Tambor" en 750 a 18px y "Libro de operaciones" condensado (ancho 75) en tinta segunda. A la derecha la luz de salud con su texto (13px) y el enlace a Phoenix en tinta azul 600 con flecha diagonal.

### Sellos de estado (componente firma del sistema)
El estado del trabajo se lee como un sello de goma, no como un badge.
- **Forma:** doble borde de la tinta del estado, texto en mayúsculas condensado, padding 2px 7px, girado −2° cuando está suelto, derecho en serie.
- **Tintas:** En cola gris, En curso azul, Espera firma violeta relleno con texto hoja, Resuelto verde, Falló rojo.
- **En curso:** lleva a la derecha un trazo discontinuo de 14px que corre (700ms, lineal, infinito): la tinta todavía está fresca.
- **Sello grande:** 15px, padding 5px 12px, borde de 2.5px, girado −4°, flotando en la cabeza del comprobante. Cuando cambia el estado se estampa: entra desde 1.7× de escala, −9° y desenfocado, y asienta en 260ms.

### Historial de sellos
Los sellos que recibió el comprobante, en orden, cada uno con su hora en mono a 12px en tinta gastada. Aparece desde el segundo sello y entonces reemplaza los datos "Asentado" y "Último cambio", porque las horas ya lo dicen. Solo registra lo que la consola vio. Sellos chicos, derechos y quietos, en una fila que envuelve con 8px por 16px de separación.

### Libro de asientos
- **Fila:** botón de ancho completo, grilla número / pedido / sello, mínimo 52px, línea inferior en renglón. Una doble regla roja vertical de 1px (al 55%) corre a la altura del número, como el margen de un libro contable.
- **Actual:** fondo hoja y número en violeta 600.
- **Nueva:** se asienta bajando 6px y apareciendo en 260ms.
- **Pedido:** una línea con elipsis; debajo hora y duración en mono a 11.5px.

### Firma (aprobación humana)
El momento serio de la consola.
- **Bloque:** doble borde violeta (1.5px más contorno de 1px a 3px), plano sobre la hoja, sin velo de fondo, padding 24px. Título en violeta hondo.
- **Contenido:** pregunta del sistema, cobros afectados como etiquetas mono con borde violeta sobre hoja, el análisis de los agentes completo (sin caja ni scroll propio, abierto por un filete de renglón y en prosa de 14px) para leerlo entero antes de firmar, y el formulario "Quién firma" + comentario con los botones Aprobar y firmar (sello) y Rechazar (rojo).

### Recorrido
Un segundo libro dentro del comprobante: cada paso es una fila de tres pistas (agente en label condensado de 112px, qué hizo, tiempo en mono). El supervisor se rotula en tinta plena a 750; un paso con error, en rojo. Las herramientas usadas van como etiquetas mono con borde de renglón.

### Aviso
Franja de tinta con texto hoja a 13.5px, padding 12px 16px, sin radio, con la sombra de aviso flotante. Aparece y se va con un fundido y 8px de desplazamiento en 200ms. Vive al pie de la columna del libro (ver Layout).

## Do's and Don'ts

### Do:
- **Do** reservar el violeta de sello (#4b3b8f) para acciones y decisiones humanas: botón primario, firma, estado Espera firma, foco.
- **Do** mostrar todo estado de trabajo como sello de doble borde con la tinta de su estado, y rellenar solo "Espera firma".
- **Do** escribir números, IDs, horas y duraciones en JetBrains Mono con cifras tabulares.
- **Do** separar la cabeza de un documento de su cuerpo con la doble regla roja de 3px, y marcar su margen con la doble regla vertical.
- **Do** dividir listas con líneas de 1px en renglón (#b9cbd8) en lugar de cajas o fondos alternados.
- **Do** bordear todo control con borde de control (#6d8aa1) para que llegue a 3:1 sobre el papel.
- **Do** condensar los rótulos con el eje de ancho de Archivo (72–85) y dejar la prosa en ancho 100.
- **Do** dejar que el análisis que precede a una firma se lea entero, sin caja ni scroll propio.
- **Do** limitar el hover a dispositivos con puntero fino y reducir toda animación a un fundido cuando el usuario pide menos movimiento.

### Don't:
- **Don't** redondear esquinas de superficies, campos, botones, chips ni sellos (radio 0); la luz de salud es el único círculo.
- **Don't** convertir los estados de un trabajo en badges tipo píldora ni en puntos de color sueltos (el punto es solo para la salud de la API).
- **Don't** sumar una segunda tinta de acento ni usar el violeta como decoración, velo de fondo o color de rótulo.
- **Don't** levantar con sombra nada que no sea el comprobante abierto o el aviso flotante.
- **Don't** girar sellos que están en una serie, ni dejar correr la tinta fresca en sellos de historial o de ejemplo.
- **Don't** usar el rojo de la regla (#c4524b) para texto ni el rojo de error (#b2332c) para reglas.
- **Don't** poner el aviso sobre la hoja, donde tapa el sello grande o la firma.
- **Don't** presentar la respuesta del sistema como burbuja de chat ni como grilla de tarjetas: se lee como asiento en un comprobante.
