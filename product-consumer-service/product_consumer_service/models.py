from typing import Optional
from sqlmodel import Field, SQLModel

class ProductUpdate(SQLModel):
    name: Optional[str] = None
    product_id: Optional[int] = None
    description: Optional[str] = None
    price: Optional[float] = None
    category: Optional[str] = None
    in_stock: Optional[bool] = False


class Product(ProductUpdate, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)