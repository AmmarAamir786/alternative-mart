from typing import Optional
from pydantic import BaseModel
from sqlmodel import Field, SQLModel

class ProductUpdate(BaseModel):
    name: Optional[str]
    product_id: Optional[int]
    description: Optional[str]
    price: Optional[float]
    category: Optional[str]
    in_stock: Optional[bool]


class Product(ProductUpdate):
    id: Optional[int]