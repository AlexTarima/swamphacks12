from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import httpx
from fastapi import Query

app = FastAPI(title="PlanIT Control Backend")

# Allow a locally running Vite frontend to call this backend.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

# Example data for our first version.
sites = {
    1: {
        "id": 1,
        "name": "Demo Job Site",
        "latitude": 25.75,
        "longitude": -80.70,
    }
}

# Local machine will display whats in function if web url ends in what is in quotes
@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/sites")
def get_sites():
    return list(sites.values())

# gets site_id from the url itself and then uses that in function
@app.get("/api/sites/{site_id}")
def get_site(site_id: int):
    if site_id not in sites:
        raise HTTPException(
            status_code=404,
            detail="Site not found",
        )

    return sites[site_id]
# API for weather data
def fetch_weather(latitude: float, longitude: float):
    url = "https://api.open-meteo.com/v1/forecast"

    # These become parameters in the request URL.
    # Specifies the location, requested measurements, and units
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "current": (
            "temperature_2m,"
            "relative_humidity_2m,"
            "precipitation,"
            "wind_speed_10m,"
            "wind_gusts_10m"
        ),
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "precipitation_unit": "inch",
        "timezone": "auto",
    }
# Use parameters that were just made and call the meteo API
# with httpx. 
    try:
        response = httpx.get(url, params=params, timeout=10.0)
        # If http error raise it
        response.raise_for_status()
    except httpx.HTTPError as error:
        raise HTTPException(
            status_code=502,
            detail="Could not retrieve weather from Open-Meteo.",
        ) from error
    # Store data retrieved inside a json file
    data = response.json()

    return {
        "source": "Open-Meteo",
        "requested_coordinates": {
            "latitude": latitude,
            "longitude": longitude,
        },
        "timezone": data["timezone"],
        "current": data["current"],
        "units": data["current_units"],
    }
# First endpoint
# Query makes the coordinate, and ge an le ensure valid coordinates
@app.get("/api/weather")
def get_weather(
    latitude: float = Query(..., ge=-90, le=90),
    longitude: float = Query(..., ge=-180, le=180),
):
    return fetch_weather(latitude, longitude)


@app.get("/api/sites/{site_id}/weather")
def get_site_weather(site_id: int):
    if site_id not in sites:
        raise HTTPException(
            status_code=404,
            detail="Site not found",
        )

    site = sites[site_id]

    weather = fetch_weather(
        latitude=site["latitude"],
        longitude=site["longitude"],
    )

    return {
        "site_id": site_id,
        "site_name": site["name"],
        "weather": weather,
    }