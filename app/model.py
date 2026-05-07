from sqlalchemy import Column, Integer, String, DateTime, Boolean, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
import datetime

Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    phone_number = Column(String)
    # The core of the "Terminus" logic:
    last_heartbeat = Column(DateTime, default=datetime.datetime.utcnow)
    is_dead = Column(Boolean, default=False)
    
    # Relationship to vault items
    assets = relationship("VaultItem", back_populates="owner")

class VaultItem(Base):
    __tablename__ = "vault_items"
    id = Column(Integer, primary_key=True, index=True)
    asset_name = Column(String)
    encrypted_data = Column(String) # For keys/messages
    beneficiary_wallet = Column(String)
    owner_id = Column(Integer, ForeignKey("users.id"))
    
    owner = relationship("User", back_populates="assets")