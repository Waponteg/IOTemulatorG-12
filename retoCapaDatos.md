# Reto Capa de Datos — Nuevo Endpoint: Resumen de Actividad por Estación

## Descripción General

Se agregó un nuevo endpoint **`GET /api/station-summary/`** en ambas aplicaciones (PostgreSQL y TimescaleDB) que devuelve un resumen completo de actividad por estación para un rango de tiempo dado.

La consulta involucra las siguientes entidades relacionadas:

- **Data** → **Station** → **User**
- **Station** → **Location** → **City**, **State**, **Country**
- **Data** → **Measurement**

### Parámetros de entrada

| Parámetro | Tipo | Descripción |
|-----------|------|-------------|
| `from` | string (yyyymmdd) | Inicio del rango de tiempo. Ejemplo: `20210601`. Si se omite, se usa una semana atrás. |
| `to` | string (yyyymmdd) | Fin del rango de tiempo. Ejemplo: `20210630`. Si se omite, se usa la fecha actual. |

**Ejemplo de consumo:** `GET /api/station-summary/?from=20210601&to=20210630`

### Estructura de la respuesta JSON

```json
{
  "stations": [
    {
      "station_id": 1,
      "user": "ja.avelino",
      "location": {
        "city": "Cajica",
        "state": "Cundinamarca",
        "country": "Colombia",
        "lat": 4.91,
        "lng": -74.02
      },
      "last_activity": "2026-02-22T10:30:00",
      "measurements": [
        {
          "name": "temperatura",
          "unit": "°C",
          "count": 150,
          "min": 10.5,
          "max": 35.2,
          "avg": 22.3,
          "last_value": 23.1,
          "last_time": "2026-02-22T10:30:00"
        }
      ]
    }
  ],
  "total_stations": 1,
  "time_range": {
    "from": "15/02/2026 00:00",
    "to": "23/02/2026 00:00"
  }
}
```

---

## Aplicación PostgreSQL (`postgresMonitoring/`)

### Archivos modificados

- `realtimeGraph/views.py` — Se agregó la función `station_summary`.
- `realtimeGraph/urls.py` — Se registró la ruta `api/station-summary/`.

### Cómo funciona la consulta

En PostgreSQL estándar, el modelo `Data` almacena **una fila por cada lectura individual** con los campos:

- `value`: un único valor `FloatField`
- `time`: `DateTimeField` usado como clave primaria

#### Lógica de la consulta

1. Se obtienen todas las estaciones con `select_related` para cargar en una sola consulta la información de `User`, `Location`, `City`, `State` y `Country`.
2. Para cada estación y cada tipo de medición, se filtran los registros de `Data` por rango de tiempo.
3. Las estadísticas se calculan **en tiempo de ejecución** usando las funciones de agregación de Django:
   ```python
   stats = data_qs.aggregate(
       min_val=Min('value'),
       max_val=Max('value'),
       avg_val=Avg('value'),
   )
   ```
4. El conteo se obtiene con `data_qs.count()`, que cuenta filas individuales.
5. El último valor se obtiene ordenando por tiempo descendente:
   ```python
   last_record = data_qs.order_by('-time').first()
   ```

---

## Aplicación TimescaleDB (`realtimeMonitoring/`)

### Archivos modificados

- `realtimeGraph/views.py` — Se agregó la función `station_summary`.
- `realtimeGraph/urls.py` — Se registró la ruta `api/station-summary/`.

### Cómo funciona la consulta

En TimescaleDB, el modelo `Data` utiliza un esquema optimizado para series de tiempo que **agrupa múltiples lecturas en una sola fila** (chunk por hora). Los campos relevantes son:

- `time`: `BigIntegerField` en microsegundos (epoch) como clave primaria
- `base_time`: `DateTimeField` que marca el inicio de la hora
- `values`: `ArrayField` con todos los valores de esa hora
- `times`: `ArrayField` con los offsets en segundos dentro de la hora
- `min_value`, `max_value`, `avg_value`: estadísticas **pre-calculadas** al insertar datos
- `length`: cantidad de lecturas en ese chunk

#### Lógica de la consulta

1. Se obtienen las estaciones igual que en PostgreSQL, con `select_related`.
2. El filtro temporal usa **timestamps en microsegundos** en lugar de objetos `datetime`:
   ```python
   start_ts = int(start.timestamp() * 1000000)
   end_ts = int(end.timestamp() * 1000000)
   data_qs = Data.objects.filter(time__gte=start_ts, time__lte=end_ts, ...)
   ```
3. Las estadísticas se obtienen directamente de los **campos pre-calculados** de cada chunk, sin necesidad de agregar con SQL:
   ```python
   overall_min = min(c.min_value for c in chunks)
   overall_max = max(c.max_value for c in chunks)
   overall_avg = sum(c.avg_value * c.length for c in chunks) / total_length
   ```
4. El conteo total de lecturas se calcula sumando el `length` de cada chunk:
   ```python
   total_length = sum(c.length for c in chunks)
   ```
5. El último valor se extrae del **último elemento del array `values`** del chunk más reciente:
   ```python
   last_chunk = max(chunks, key=lambda c: c.time)
   last_value = last_chunk.values[-1]
   ```

---

## Tabla comparativa de diferencias

| Aspecto | PostgreSQL Estándar | TimescaleDB |
|---------|---------------------|-------------|
| **Modelo de datos** | 1 fila = 1 lectura individual | 1 fila = N lecturas agrupadas por hora (arrays) |
| **Filtro temporal** | `time__gte=start` con `DateTimeField` | `time__gte=start_ts` con `BigIntegerField` (microsegundos) |
| **Cálculo de min/max/avg** | Función `aggregate()` de Django ejecutada en runtime sobre todas las filas | Lectura directa de campos pre-calculados `min_value`, `max_value`, `avg_value` |
| **Conteo de lecturas** | `data_qs.count()` — cuenta filas en la BD | `sum(c.length)` — suma la cantidad de valores dentro de los arrays |
| **Último valor** | `.order_by('-time').first().value` — una fila individual | `max(chunks).values[-1]` — último elemento del array del chunk más reciente |
| **Filas procesadas** | Potencialmente miles (una por lectura de sensor) | Pocas filas (una por hora por estación/variable) |
| **Carga en la BD** | Alta: el motor SQL recorre y agrega muchas filas | Baja: pocos registros con datos ya resumidos |
| **Compresión** | No disponible | Compresión automática por TimescaleDB cada 7 días |
| **Particionamiento** | Tabla única sin particiones | Hipertabla con chunks de 3 días |

---

## Código implementado

### URLs configuradas (ambas aplicaciones idénticas)

#### `realtimeGraph/urls.py`

```python
from django.urls import path
from django.views.decorators.csrf import csrf_exempt

from .views import *

urlpatterns = [
    path('', DashboardView.as_view(), name='dashboard'),
    path('historical/', HistoricalView.as_view(), name='historical'),
    path('rema/', RemaView.as_view(), name='rema'),
    path('rema/<str:measure>', RemaView.as_view(), name='rema'),
    path("mapJson/", get_map_json, name="mapJson"),
    path("mapJson/<str:measure>", get_map_json, name="mapJson"),
    path('login/', LoginView.as_view(), name='login'),
    path('logout/', LogoutView.as_view(), name='logout'),
    path('historical/data', download_csv_data, name='historical-data'),
    # Nueva ruta para el endpoint de resumen de actividad por estación
    path('api/station-summary/', station_summary, name='station-summary'),
]
```

---

## PostgreSQL — Función `station_summary`

### `realtimeGraph/views.py`

```python
def station_summary(request):
    """
    Endpoint: GET /api/station-summary/
    
    Devuelve un resumen de actividad por estación para un rango de tiempo.
    
    Parámetros de entrada:
    - from (yyyymmdd): inicio del rango (ejemplo: 20210601). Default: semana atrás.
    - to (yyyymmdd): fin del rango (ejemplo: 20210630). Default: hoy.
    
    Retorna JSON con información de cada estación, municipios, usuarios y
    estadísticas de mediciones (min, max, avg, count, último valor).
    """
    
    # PASO 1: Parsear fechas desde formato yyyymmdd
    from_param = request.GET.get('from', None)
    to_param = request.GET.get('to', None)
    try:
        # Si se proporciona 'from', parsea como yyyymmdd; si no, usa semana atrás
        start = datetime.strptime(from_param, '%Y%m%d') if from_param else datetime.now() - dateutil.relativedelta.relativedelta(weeks=1)
    except ValueError:
        return JsonResponse({'error': 'Formato de fecha inválido. Use yyyymmdd, ejemplo: 20210601'}, status=400)
    try:
        # Si se proporciona 'to', parsea como yyyymmdd; si no, usa mañana (incluye todo hoy)
        end = datetime.strptime(to_param, '%Y%m%d') if to_param else datetime.now() + dateutil.relativedelta.relativedelta(days=1)
    except ValueError:
        return JsonResponse({'error': 'Formato de fecha inválido. Use yyyymmdd, ejemplo: 20210601'}, status=400)

    # PASO 2: Cargar todas las estaciones con sus relaciones en una sola consulta
    # (select_related evita N+1 queries)
    stations = Station.objects.select_related(
        'user', 'location', 'location__city', 'location__state', 'location__country'
    ).all()

    # PASO 3: Obtener todas las variables de medición
    measurements = Measurement.objects.all()
    result = []

    # PASO 4: Iterar por cada estación
    for station in stations:
        # Preparar estructura de datos de la estación
        station_data = {
            'station_id': station.id,
            'user': station.user.login,
            'location': {
                'city': station.location.city.name,
                'state': station.location.state.name,
                'country': station.location.country.name,
                'lat': float(station.location.lat) if station.location.lat else None,
                'lng': float(station.location.lng) if station.location.lng else None,
            },
            'last_activity': station.last_activity.isoformat() if station.last_activity else None,
            'measurements': [],
        }

        # PASO 5: Iterar por cada tipo de medición
        for measure in measurements:
            # Filtrar datos de esta estación y variable en el rango de fechas
            # En PostgreSQL, cada row es una medición individual
            data_qs = Data.objects.filter(
                station=station,
                measurement=measure,
                time__gte=start,
                time__lte=end,
            )
            count = data_qs.count()
            # Si no hay datos para esta combinación, continuar con la siguiente
            if count == 0:
                continue

            # PASO 6: Calcular estadísticas usando agregación de Django
            # Esto ejecuta operaciones MIN, MAX, AVG en la base de datos
            stats = data_qs.aggregate(
                min_val=Min('value'),
                max_val=Max('value'),
                avg_val=Avg('value'),
            )
            # Obtener el último registro ordenado por tiempo
            last_record = data_qs.order_by('-time').first()

            # PASO 7: Agregar la medición al resumen de la estación
            station_data['measurements'].append({
                'name': measure.name,
                'unit': measure.unit,
                'count': count,  # Cantidad de filas (lecturas individuales)
                'min': stats['min_val'],
                'max': stats['max_val'],
                'avg': round(stats['avg_val'], 2) if stats['avg_val'] else 0,
                'last_value': last_record.value if last_record else None,
                'last_time': last_record.time.isoformat() if last_record else None,
            })

        # PASO 8: Solo agregar estación al resultado si tiene mediciones en el rango
        if station_data['measurements']:
            result.append(station_data)

    # PASO 9: Construir respuesta JSON
    response = {
        'stations': result,
        'total_stations': len(result),
        'time_range': {
            'from': start.strftime('%d/%m/%Y %H:%M'),
            'to': end.strftime('%d/%m/%Y %H:%M'),
        },
    }
    return JsonResponse(response)
```

---

## TimescaleDB — Función `station_summary`

### `realtimeGraph/views.py`

```python
def station_summary(request):
    """
    Endpoint: GET /api/station-summary/
    
    Devuelve un resumen de actividad por estación para un rango de tiempo.
    VERSIÓN OPTIMIZADA PARA TIMESCALEDB.
    
    Parámetros de entrada:
    - from (yyyymmdd): inicio del rango (ejemplo: 20210601). Default: semana atrás.
    - to (yyyymmdd): fin del rango (ejemplo: 20210630). Default: hoy.
    
    Diferencia principal con PostgreSQL:
    - TimescaleDB almacena múltiples lecturas en arrays dentro de una fila (chunk por hora)
    - Las estadísticas están PRE-CALCULADAS al insertar datos (min_value, max_value, avg_value)
    - Solo necesita procesar pocos chunks en lugar de muchas filas individuales
    """
    
    # PASO 1: Parsear fechas desde formato yyyymmdd (igual que PostgreSQL)
    from_param = request.GET.get('from', None)
    to_param = request.GET.get('to', None)
    try:
        start = datetime.strptime(from_param, '%Y%m%d') if from_param else datetime.now() - dateutil.relativedelta.relativedelta(weeks=1)
    except ValueError:
        return JsonResponse({'error': 'Formato de fecha inválido. Use yyyymmdd, ejemplo: 20210601'}, status=400)
    try:
        end = datetime.strptime(to_param, '%Y%m%d') if to_param else datetime.now() + dateutil.relativedelta.relativedelta(days=1)
    except ValueError:
        return JsonResponse({'error': 'Formato de fecha inválido. Use yyyymmdd, ejemplo: 20210601'}, status=400)

    # PASO 2: Convertir a microsegundos (formato nativo de TimescaleDB)
    # TimescaleDB almacena el timestamp en microsegundos (epoch)
    start_ts = int(start.timestamp() * 1000000)
    end_ts = int(end.timestamp() * 1000000)

    # PASO 3: Cargar todas las estaciones con sus relaciones (igual que PostgreSQL)
    stations = Station.objects.select_related(
        'user', 'location', 'location__city', 'location__state', 'location__country'
    ).all()

    # PASO 4: Obtener todas las variables de medición
    measurements = Measurement.objects.all()
    result = []

    # PASO 5: Iterar por cada estación
    for station in stations:
        # Preparar estructura de datos de la estación (igual que PostgreSQL)
        station_data = {
            'station_id': station.id,
            'user': station.user.login,
            'location': {
                'city': station.location.city.name,
                'state': station.location.state.name,
                'country': station.location.country.name,
                'lat': float(station.location.lat) if station.location.lat else None,
                'lng': float(station.location.lng) if station.location.lng else None,
            },
            'last_activity': station.last_activity.isoformat() if station.last_activity else None,
            'measurements': [],
        }

        # PASO 6: Iterar por cada tipo de medición
        for measure in measurements:
            # Filtrar chunks (filas) de TimescaleDB para esta estación y variable
            # IMPORTANTE: Usar timestamp en microsegundos, no DateTimeField
            data_qs = Data.objects.filter(
                station=station,
                measurement=measure,
                time__gte=start_ts,
                time__lte=end_ts,
            )
            # Convertir QuerySet a lista para iterar (chunks = registros horarios)
            chunks = list(data_qs)
            if not chunks:
                continue

            # PASO 7: Calcular estadísticas desde los campos PRE-CALCULADOS
            # No usamos aggregate() de Django; leemos directo los campos calculados
            # Cada chunk tiene: min_value, max_value, avg_value, length (cantidad de lecturas en la hora)
            
            # Obtener min global de todos los chunks
            overall_min = min(c.min_value for c in chunks if c.min_value is not None)
            # Obtener max global de todos los chunks
            overall_max = max(c.max_value for c in chunks if c.max_value is not None)
            # Contar total de lecturas sumando el length de cada chunk
            total_length = sum(c.length for c in chunks)
            # Calcular promedio ponderado: suma(avg*length) / total_length
            overall_avg = (
                sum(c.avg_value * c.length for c in chunks if c.avg_value is not None) / total_length
                if total_length > 0 else 0
            )

            # PASO 8: Obtener el último valor
            # En TimescaleDB, los datos están en arrays dentro de chunks
            # El chunk más reciente contiene los datos más nuevos
            last_chunk = max(chunks, key=lambda c: c.time)
            # El último valor es el último elemento del array 'values'
            last_value = last_chunk.values[-1] if last_chunk.values else None
            # El último tiempo es base_time + el offset en segundos del array 'times'
            last_time_offset = last_chunk.times[-1] if last_chunk.times else 0
            last_time_epoch = last_chunk.base_time.timestamp() + last_time_offset
            last_time_str = datetime.fromtimestamp(last_time_epoch).isoformat()

            # PASO 9: Agregar la medición al resumen de la estación
            station_data['measurements'].append({
                'name': measure.name,
                'unit': measure.unit,
                'count': total_length,  # Total de lecturas (suma de lengths de chunks)
                'min': overall_min,
                'max': overall_max,
                'avg': round(overall_avg, 2),
                'last_value': last_value,
                'last_time': last_time_str,
            })

        # PASO 10: Solo agregar estación al resultado si tiene mediciones en el rango
        if station_data['measurements']:
            result.append(station_data)

    # PASO 11: Construir respuesta JSON
    response = {
        'stations': result,
        'total_stations': len(result),
        'time_range': {
            'from': start.strftime('%d/%m/%Y %H:%M'),
            'to': end.strftime('%d/%m/%Y %H:%M'),
        },
    }
    return JsonResponse(response)
```

---

## Conclusión

Ambas implementaciones logran el mismo resultado funcional (mismo JSON de respuesta), pero la versión TimescaleDB es más eficiente para datos IoT de series de tiempo porque:

1. **Reduce la cantidad de filas** que debe consultar la base de datos (chunks horarios vs lecturas individuales).
2. **Evita cálculos de agregación en runtime** al usar estadísticas pre-calculadas en la escritura.
3. **Aprovecha la compresión y particionamiento** nativo de TimescaleDB para manejar grandes volúmenes de datos históricos.
