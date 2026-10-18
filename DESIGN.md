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
    backgroundColor: "rgba(75, 59, 143, 0.08)"
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
---

# Design System: Tambor · Libro de operaciones

## Overview

**Creative North Star: "El libro de operaciones y el sello"**

La consola es el libro de un back office de cobros. Cada pedido a la API es un comprobante numerado que se asienta en un libro de renglones azules y recibe sellos de goma a medida que cambia de estado. El papel es contable, verde pálido; la tinta es verde-negra; y hay una sola tinta de acento, el violeta de los sellos, que marca lo que pide una mano humana: encolar, firmar, decidir.

La densidad es la de una planilla de oficina, no la de un panel de marketing: líneas de 1px en lugar de tarjetas, números en monoespaciada tabular, encabezados en Archivo condensado. La jerarquía la dan el ancho de la letra, el peso y las reglas (renglón azul, doble regla roja de margen), no los fondos de color ni las sombras. La única hoja que se levanta del papel es el comprobante abierto.

Rechazos confirmados por el contrato de dirección: el panel SaaS de tarjetas redondeadas y el chat de burbujas. La respuesta del sistema se lee como un asiento, no como un mensaje.

**Key Characteristics:**
- Papel verde pálido en tres capas (papel, papel-2, hoja) y tinta verde-negra en tres intensidades.
- Una sola tinta de acento: violeta de sello, reservada a la acción y a la firma.
- Estados como sellos de goma de doble borde, cada uno con su tinta, nunca como badges redondeados.
- Cero radios en toda superficie y control.
- Archivo variable en anchos condensados para rótulos; JetBrains Mono tabular para todo lo que es número o ID.
- Doble regla roja que separa la cabeza del cuerpo de cada documento.

## Colors

Papel contable y tintas de oficina: neutros verdosos de baja croma, renglones azul pizarra, una regla roja y un violeta de sello como única voz de acento.

### Primary
- **Violeta de sello** (#4b3b8f): botón primario (Encolar, Aprobar y firmar), bloque de firma, sello "Espera firma" (el único sello relleno), anillo de foco, cursor de texto, selección, número del asiento abierto, rótulo del supervisor en el recorrido y el chip de ejemplo que dispara un reembolso.
- **Violeta hondo** (#3a2d70): hover del botón de sello y título del bloque de firma. Nunca aparece solo, siempre como variante del violeta.

### Neutral
- **Papel** (#edf1e6): fondo de la página, la hoja contable.
- **Papel de libro** (#e2e8da): segunda capa, la barra superior y el brillo del esqueleto de carga.
- **Hoja de comprobante** (#f8faf4): superficie de lo que se escribe o se lee de cerca: el comprobante, los campos, la fila activa del libro, el análisis dentro de la firma.
- **Tinta** (#1c2420): texto principal, verde-negro. También fondo del aviso flotante.
- **Tinta segunda** (#4a5650): texto secundario, etiquetas de campo, rótulos de columna, datos del comprobante (6.9:1 sobre papel).
- **Tinta gastada** (#5e6963): ayudas, placeholders, metadatos de fila, contador (5:1 sobre papel).
- **Renglón** (#b9cbd8): líneas entre filas, renglones del área de texto, divisores internos de tablas y pasos.
- **Renglón fuerte** (#7f9db4): bordes estructurales: barra, columna, bordes de campos y del comprobante, barra de scroll.
- **Regla roja** (#c4524b): solo como línea, nunca como texto: doble regla de 3px bajo la cabeza de la boleta y del comprobante, y el margen doble del libro (al 55% de opacidad).

### Tintas de estado
- **Tinta azul** (#2c5a85): estado En curso y enlaces (Phoenix, desplegables "Ver aporte").
- **Tinta verde** (#2d6a4a): estado Resuelto, luz de API en línea, acciones hechas.
- **Tinta roja** (#b2332c): estado Falló, errores de campo, alertas, botón Rechazar, luz de API caída.
- **Tinta gris** (#5c655f): estado En cola y luz de salud mientras conecta.

### Named Rules
**La Regla de la Tinta Única.** El violeta de sello es el único acento. Aparece donde hay una acción o una decisión humana, y en ningún otro lugar. Si una pantalla nueva necesita "un color para destacar", la respuesta es peso o ancho tipográfico, no otra tinta.

**La Regla de una Tinta por Estado.** Cada estado tiene una tinta fija (gris en cola, azul en curso, violeta espera firma, verde resuelto, rojo falló) y esa tinta colorea borde, doble borde y texto del sello. Solo "Espera firma" va relleno, porque es el único estado que le pide algo a la persona.

**La Regla de la Regla Roja.** El rojo de margen (#c4524b) y el rojo de error (#b2332c) son tintas distintas. La regla es papelería: separa cabeza de cuerpo. El rojo de error es un dato. No se intercambian.

## Typography

**Display Font:** Archivo (variable, ancho 62–125, peso 300–800), con ui-sans-serif y system-ui de respaldo
**Body Font:** Archivo, mismo archivo, en ancho 100
**Label/Mono Font:** JetBrains Mono (400 y 600), con ui-monospace y Cascadia Mono de respaldo

**Character:** una grotesca de oficina que se condensa para hablar como encabezado de planilla y se abre para la prosa, junto a una monoespaciada que hace de máquina de numerar. El ancho variable es la herramienta de jerarquía principal, antes que el tamaño.

### Hierarchy
- **Display** (760, 48px, interlínea 1, ancho 72, cifras tabulares): solo el Nº del comprobante abierto. Baja a 34px por debajo de 960px.
- **Headline** (750, 20–24px, -0.015em): títulos de documento: "Nueva orden" (22px), estado vacío (24px), bloque de firma (20px).
- **Title** (700, 17px, -0.01em): apartados del comprobante (Respuesta, Recorrido, Reembolso).
- **Body** (400, 15px, 1.55): texto corriente. El pedido citado en el comprobante sube a 19px y peso 450 con medida de 62ch; la prosa de la respuesta va a 15px, interlínea 1.6 y 72ch.
- **Label** (600, 13px): etiquetas de campo y avisos.
- **Label condensado** (650–700, 11.5–13px, ancho 80): rótulos de columna y de dato ("Asientos", "Asentado", "Duración", nombre del agente en el recorrido).
- **Sello** (700, 10.5px, ancho 85, 0.06em, mayúsculas): texto de los sellos de estado; 15px en el sello grande del comprobante.
- **Mono** (400, 11.5–13px, tabular): números de asiento, IDs de trabajo y de cobro, horas, duraciones, contador de caracteres, herramientas usadas.

### Named Rules
**La Regla del Número a Máquina.** Todo número o identificador (Nº de asiento, `cob_XXXXX`, horas, duraciones, contadores) va en JetBrains Mono con cifras tabulares. La única excepción es el Nº grande del comprobante, que va en Archivo condensado con `tnum`, porque es el titular del documento.

**La Regla del Ancho Condensado.** Los rótulos se condensan (ancho 72–85); el texto que se lee de corrido nunca. Si algo es una etiqueta de columna o de sello, se angosta; si es una oración, va en ancho 100.

## Layout

Una mesa de dos columnas bajo una barra fija. La barra mide 56px, es sticky y lleva marca, salud de la API y el enlace a Phoenix. La columna izquierda (entre 340 y 420px) apila la boleta "Nueva orden" y el libro de asientos, que scrollea por su cuenta; una línea de 1px en renglón fuerte la separa de la hoja derecha. La hoja centra el comprobante abierto con un ancho máximo de 880px.

El ritmo de espaciado es de base 4 (4, 8, 12, 16, 24, 32, 48px). Los márgenes de bloque usan 24px en la columna y 32px dentro del comprobante; entre apartados del comprobante, 32px. El área de texto de la boleta trabaja sobre un renglón de 28px: la interlínea y las líneas dibujadas coinciden, y la altura mínima es de cuatro renglones.

Las filas del libro son una grilla de tres pistas (número de 40px, texto flexible, sello) con mínimo de 52px de alto. Los datos del comprobante van en una grilla de cuatro columnas de ancho propio.

Responsive:
- **≤960px:** una sola columna; el libro se limita a 320px de alto con scroll propio; el comprobante baja a 24px de padding; el Nº a 34px; los datos a dos columnas.
- **≤600px:** márgenes laterales de 16px; se ocultan "Libro de operaciones" y "Trazas en" (queda "Phoenix"); el texto de salud queda solo para lectores de pantalla; el sello del comprobante sube por encima del Nº; los pasos del recorrido y el formulario de firma se apilan.

## Elevation & Depth

El sistema es plano: la profundidad se dibuja con líneas (renglón, renglón fuerte, doble regla) y con tres capas tonales de papel. Hay exactamente una superficie levantada, el comprobante abierto, con una sombra baja y difusa que lo despega del papel como una hoja apoyada encima. El aviso flotante (toast) tiene su propia sombra porque vive por encima de todo y es transitorio.

### Shadow Vocabulary
- **Hoja apoyada** (`box-shadow: 0 1px 0 rgba(28, 36, 32, 0.04), 0 12px 28px -18px rgba(44, 70, 58, 0.35)`): solo el comprobante.
- **Aviso flotante** (`box-shadow: 0 10px 24px -12px rgba(28, 36, 32, 0.6)`): solo el toast.
- **Filete de foco** (`box-shadow: 0 0 0 1px #4b3b8f`): campos de texto en foco, sumado al borde violeta; duplica el trazo a 2px sin mover el layout.

### Named Rules
**La Regla de la Hoja Única.** Una sola cosa por pantalla se levanta del papel: el documento que se está leyendo. Filas, campos, boleta, firma y sellos quedan planos y se separan con líneas.

## Shapes

Esquinas a escuadra en todo: superficies, campos, botones, chips, sellos y la firma (radio 0). La única forma redonda del sistema es la luz de salud de la API, un punto de 8px, porque es una luz y no un papel.

La forma característica es el **doble borde de sello**: un borde de 1.5–2.5px más un `outline` de 1–1.5px separado por 1.5–3px, de la misma tinta. Lo usan los sellos de estado, el sello "T" de la marca y el bloque de firma. Los sellos sueltos van girados (−2° los chicos, −4° el grande y la marca); dentro de una columna de filas van derechos.

Las líneas también son forma: 1px para renglones y bordes, doble 3px en la regla roja, discontinuo 1px para los chips de ejemplo (que se vuelven continuos al pasar el puntero).

### Named Rules
**La Regla del Cero Radio.** Nada de papel tiene esquinas redondeadas. Si un componente nuevo necesita suavidad, se resuelve con espacio o con tinta más clara, nunca con `border-radius`.

**La Regla del Sello Derecho en Columna.** Un sello solo se gira cuando está solo. Ocho sellos girados iguales en una lista se leen como patrón decorativo; en el libro van rectos.

## Components

### Buttons
Firmes y planos, como un sello que se apoya.
- **Shape:** esquinas rectas (0), alto de 40px, padding horizontal de 16px, Archivo 650 a 14px con 0.01em.
- **Primario (sello):** fondo violeta de sello, texto en hoja, icono de flecha o de sello a la derecha del texto.
- **Hover / Focus:** el fondo pasa a violeta hondo (solo con puntero fino). Foco visible con contorno violeta de 2px separado 2px. Al presionar se achica a 0.97 en 140ms. Ocupado: el texto se desenfoca 1.5px y baja a 60% de opacidad. Deshabilitado: 55% de opacidad.
- **Rechazar (rojo):** fondo transparente, texto rojo y filete interior de 1px rojo; hover con velo rojo al 6%.
- **Botón texto:** sin caja, tinta gastada, subrayado a 3px ("Limpiar libro").

### Chips (pedidos de ejemplo)
- **Style:** fondo transparente, borde discontinuo de 1px en renglón fuerte, texto en tinta segunda a 12.5px, padding 3px 8px.
- **State:** hover: borde continuo, texto en tinta, fondo hoja. La variante que lleva a una firma usa borde y texto violeta.

### Cards / Containers
El único contenedor es el **comprobante**.
- **Corner Style:** recto (0).
- **Background:** hoja sobre papel.
- **Shadow Strategy:** la Hoja apoyada (ver Elevation & Depth).
- **Border:** 1px renglón fuerte.
- **Internal Padding:** 32px (24px por debajo de 960px).
- **Cabeza:** Nº en display, pedido citado debajo, sello grande arriba a la derecha y fila de datos en mono; cierra con la doble regla roja.

### Inputs / Fields
- **Style:** fondo hoja, borde 1px renglón fuerte, esquinas rectas, 40px de alto, cursor de texto violeta.
- **Área de texto de la boleta:** dibuja renglones de libro (línea de 1px en renglón cada 28px, ligada al scroll del contenido); la interlínea es el renglón.
- **Focus:** borde violeta más filete de 1px violeta; sin contorno del navegador.
- **Error:** borde rojo y mensaje rojo de 13px debajo, anunciado como alerta.

### Navigation
- **Barra:** 56px, sticky, fondo papel de libro, borde inferior 1px renglón fuerte. A la izquierda el sello "T" (26px, doble borde violeta, girado −4°), "Tambor" en 750 a 18px y "Libro de operaciones" condensado (ancho 75) en tinta segunda. A la derecha la luz de salud con su texto (13px) y el enlace a Phoenix en tinta azul 600 con flecha diagonal.

### Sellos de estado (componente firma del sistema)
El estado del trabajo se lee como un sello de goma, no como un badge.
- **Forma:** doble borde de la tinta del estado, texto en mayúsculas condensado, padding 2px 7px, girado −2° cuando está suelto, derecho en el libro.
- **Tintas:** En cola gris, En curso azul, Espera firma violeta relleno con texto hoja, Resuelto verde, Falló rojo.
- **En curso:** lleva a la derecha un trazo discontinuo de 14px que corre (700ms, lineal, infinito): la tinta todavía está fresca.
- **Sello grande:** 15px, padding 5px 12px, borde de 2.5px, girado −4°, en la cabeza del comprobante. Cuando cambia el estado se estampa: entra desde 1.7× de escala, −9° y desenfocado, y asienta en 260ms.

### Libro de asientos
- **Fila:** botón de ancho completo, grilla número / pedido / sello, mínimo 52px, línea inferior en renglón. Una doble regla roja vertical de 1px (al 55%) corre a la altura del número, como el margen de un libro contable.
- **Actual:** fondo hoja y número en violeta 600.
- **Nueva:** se asienta bajando 6px y apareciendo en 260ms.
- **Pedido:** una línea con elipsis; debajo hora y duración en mono a 11.5px.

### Firma (aprobación humana)
El momento serio de la consola.
- **Bloque:** doble borde violeta (1.5px más contorno de 1px a 3px), velo violeta al 8% de fondo, padding 24px. Título en violeta hondo.
- **Contenido:** pregunta del sistema, cobros afectados como etiquetas mono con borde violeta sobre hoja, análisis de los agentes en un recuadro hoja con scroll propio (máximo 280px), y el formulario "Quién firma" + comentario con los botones Aprobar y firmar (sello) y Rechazar (rojo).

### Recorrido
Un segundo libro dentro del comprobante: cada paso es una fila de tres pistas (agente en label condensado de 112px, qué hizo, tiempo en mono). El supervisor se rotula en violeta; un paso con error, en rojo. Las herramientas usadas van como etiquetas mono con borde de renglón.

## Do's and Don'ts

### Do:
- **Do** reservar el violeta de sello (#4b3b8f) para acciones y decisiones humanas: botón primario, firma, estado Espera firma, foco.
- **Do** mostrar todo estado de trabajo como sello de doble borde con la tinta de su estado, y rellenar solo "Espera firma".
- **Do** escribir números, IDs, horas y duraciones en JetBrains Mono con cifras tabulares.
- **Do** separar la cabeza de un documento de su cuerpo con la doble regla roja de 3px.
- **Do** dividir listas con líneas de 1px en renglón (#b9cbd8) en lugar de cajas o fondos alternados.
- **Do** condensar los rótulos con el eje de ancho de Archivo (72–85) y dejar la prosa en ancho 100.
- **Do** limitar el hover a dispositivos con puntero fino y reducir toda animación a un fundido cuando el usuario pide menos movimiento.

### Don't:
- **Don't** redondear esquinas de superficies, campos, botones, chips ni sellos (radio 0); la luz de salud es el único círculo.
- **Don't** convertir los estados de un trabajo en badges tipo píldora ni en puntos de color sueltos (el punto es solo para la salud de la API).
- **Don't** sumar una segunda tinta de acento ni usar el violeta como decoración.
- **Don't** levantar con sombra nada que no sea el comprobante abierto o el aviso flotante.
- **Don't** girar sellos que están en una columna o lista.
- **Don't** usar el rojo de la regla (#c4524b) para texto ni el rojo de error (#b2332c) para reglas.
- **Don't** presentar la respuesta del sistema como burbuja de chat ni como grilla de tarjetas: se lee como asiento en un comprobante.
