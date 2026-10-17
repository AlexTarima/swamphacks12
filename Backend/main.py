from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import httpx
from fastapi import Query
from pydantic import BaseModel, Field
from contextlib import asynccontextmanager
import database

# Start up function. Initializes the database.
@asynccontextmanager
async def lifespan(app: FastAPI):
    database.initialize_database()
    yield


app = FastAPI(title="PlanIT Control Backend", lifespan=lifespan)

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

# defines and validates the input.
# Name must contain a non-whitespace character, latitude/longitude
# are valid
class SiteCreate(BaseModel):
    name: str = Field(min_length=1, pattern=r"\S")
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)

# defines response.
class Site(SiteCreate):
    id: int

# Local machine will display whats in function if web url ends in what is in quotes
@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/sites", response_model=list[Site])
def get_sites():
    return database.list_sites()

# Third End Point. Front end will send a json with name, latitude,
# and longitude.
# The database assigns an ID and commits the site to disk.
@app.post("/api/sites", response_model=Site, status_code=201)
def create_site(site: SiteCreate):
    return database.insert_site(**site.model_dump())

# gets site_id from the url itself and then uses that in function
@app.get("/api/sites/{site_id}", response_model=Site)
def get_site(site_id: int):
    site = database.find_site(site_id)
    if site is None:
        raise HTTPException(
            status_code=404,
            detail="Site not found",
        )

    return site
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
# First endpoint. Front end provides coordinates
# Query makes the coordinate, and ge an le ensure valid coordinates
@app.get("/api/weather")
def get_weather(
    latitude: float = Query(..., ge=-90, le=90),
    longitude: float = Query(..., ge=-180, le=180),
):
    return fetch_weather(latitude, longitude)

# Second End point. Front End provides a saved sites ID

@app.get("/api/sites/{site_id}/weather")
def get_site_weather(site_id: int):
    site = get_site(site_id)

    weather = fetch_weather(
        latitude=site["latitude"],
        longitude=site["longitude"],
    )

    return {
        "site_id": site_id,
        "site_name": site["name"],
        "weather": weather,
    }
