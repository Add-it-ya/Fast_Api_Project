from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# The pipeline one-hot encodes with handle_unknown='ignore', so an unseen
# company silently becomes an all-zero vector and the model returns a number
# that means nothing. Constraining to the trained categories turns that quiet
# wrong answer into a 422.
Company = Literal[
    'Ambassador', 'Ashok', 'Audi', 'BMW', 'Chevrolet', 'Daewoo', 'Datsun',
    'Fiat', 'Force', 'Ford', 'Honda', 'Hyundai', 'Isuzu', 'Jaguar', 'Jeep',
    'Kia', 'Land', 'Lexus', 'MG', 'Mahindra', 'Maruti', 'Mercedes-Benz',
    'Mitsubishi', 'Nissan', 'Opel', 'Peugeot', 'Renault', 'Skoda', 'Tata',
    'Toyota', 'Volkswagen', 'Volvo',
]
Owner = Literal['First', 'Second', 'Third', 'Fourth & Above', 'Test Drive Car']
Fuel = Literal['Petrol', 'Diesel', 'CNG', 'LPG']
SellerType = Literal['Individual', 'Dealer', 'Trustmark Dealer']
Transmission = Literal['Manual', 'Automatic']


class CarFeatures(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            'example': {
                'company': 'Maruti',
                'year': 2015,
                'owner': 'Second',
                'fuel': 'Petrol',
                'seller_type': 'Individual',
                'transmission': 'Automatic',
                'km_driven': 200000,
                'mileage_mpg': 55,
                'engine_cc': 1250,
                'max_power_bhp': 80,
                'torque_nm': 200,
                'seats': 5,
            }
        }
    )

    company: Company
    year: int = Field(ge=1980, le=2026, description='Year of manufacture')
    owner: Owner
    fuel: Fuel
    seller_type: SellerType
    transmission: Transmission
    km_driven: float = Field(gt=0, le=3_000_000)
    mileage_mpg: float = Field(ge=0, le=150)
    engine_cc: float = Field(gt=0, le=10_000)
    max_power_bhp: float = Field(gt=0, le=2_000)
    torque_nm: float = Field(gt=0, le=5_000)
    seats: float = Field(ge=1, le=20)


class PredictionResponse(BaseModel):
    predicted_price: str
    cached: bool
