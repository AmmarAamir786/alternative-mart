from typing import Optional
from sqlmodel import Field, SQLModel


class Product (SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    description: str
    price: float
    category: str


class ProductUpdate(SQLModel):
    name: str
    description: str
    price: float
    category: str
    