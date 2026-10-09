# Calculadora de actualización: IPC y CAC

Página web que actualiza un monto entre dos meses usando:

- **IPC** nivel general nacional (INDEC). Se actualiza sola desde la API de datos.gob.ar
  (serie `148.3_INIVELNAL_DICI_M_26`).
- **CAC** (Cámara Argentina de la Construcción): costo de construcción general, materiales y
  mano de obra. Se lee de la planilla "Años anteriores" que publica Cifras Online.

Un script (`scripts/update_data.py`) corre todos los días con GitHub Actions, agrega los meses
nuevos y vuelve a publicar la página. No hace falta tocar nada cuando sale un índice nuevo.

## Puesta en marcha (una sola vez)

1. Creá una cuenta en https://github.com y un repositorio **público** nuevo (las páginas gratuitas
   de GitHub Pages requieren repositorio público).
2. Subí el contenido de esta carpeta al repositorio (botón *Add file → Upload files*, arrastrando
   las carpetas `data`, `scripts` y `.github`, más `index.html`).
   Si tu sistema no te deja arrastrar la carpeta oculta `.github`, creá el archivo desde GitHub:
   *Add file → Create new file*, escribí la ruta `.github/workflows/actualizar.yml` y pegá el contenido.
3. En el repositorio: **Settings → Pages → Build and deployment → Source: GitHub Actions**.
4. **Settings → Actions → General → Workflow permissions → Read and write permissions** → Save.
5. **Actions → "Actualizar índices y publicar" → Run workflow**. En un minuto o dos queda publicada;
   la dirección aparece en el paso *deploy* y en Settings → Pages.

## Cómo funciona la actualización

- **IPC**: cada día el script consulta la API. Antes de agregar un mes nuevo comprueba que la
  serie guardada y la de la API varíen igual en los últimos meses; si no coinciden, no toca nada
  y avisa. Los meses nuevos se encadenan sobre el último valor guardado.
- **CAC**: cada día el script descarga la planilla de Cifras y reconstruye la serie completa
  (así se toman las correcciones de los meses provisorios). Detecta y corrige errores de carga
  de un solo mes (por ejemplo, un dígito faltante) y los deja anotados en `data/cac.json`, campo `notes`.
- Si una fuente falla, la página sigue mostrando el último dato bueno y la corrida figura en
  rojo en la pestaña Actions (GitHub también te envía un mail).

## Si la planilla de Cifras se atrasa

La planilla suele ir uno o dos meses por detrás del informe mensual. Mientras tanto podés cargar
el mes a mano en `data/cac_manual.csv`: una fila por mes con la **variación mensual en %** de
general, materiales y mano de obra, tal como sale en el informe. Al guardar el cambio en GitHub
se vuelve a publicar solo. Cuando la planilla incorpore ese mes, manda la planilla.

## Probar el script en tu computadora

```
pip install xlrd openpyxl
python scripts/update_data.py                       # todo desde internet
python scripts/update_data.py --only cac --cac-file planilla.xls
```

## Aclaraciones

- El CAC es un indicador estadístico de un edificio tipo en CABA; la Cámara aclara que cualquier
  otro uso es responsabilidad de quien lo aplica. Confirmá en tu contrato qué índice y qué mes corresponde.
- Los meses marcados como provisorios pueden cambiar.
