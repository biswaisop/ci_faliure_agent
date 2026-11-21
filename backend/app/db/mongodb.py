from pymongo.errors import ServerSelectionTimeoutError
from http import client
from fastapi.exceptions import FastAPIError
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Request
from motor.motor_asyncio import AsyncIOMotorClient

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCollection, AsyncIOMotorDatabase
import os
from dotenv import load_dotenv
import logging

load_dotenv()

logger = logging.getLogger(__name__)


class MongoDb:
    _client: AsyncIOMotorClient = None

    DB_NAME = os.getenv("MONGODB_DB_NAME", "autopci")
    
    @classmethod
    def connect(cls):
        if cls._client is None:
            uri = os.getenv("MONGODB_URI") or os.getenv("MONGODB")
            if not uri:
                raise ValueError("MONGODB_URI environment variable not set")
            cls._client = AsyncIOMotorClient(
                uri,
                maxPoolSize=100,
                minPoolSize=5,
                serverSelectionTimeoutMS=5000
            )
            logger.info("MongoDB Connected")

    @classmethod
    def disconnect(cls):
        if cls._client is not None:
            cls._client.close()
            cls._client = None
            logger.info("MongoDB Disconnected")

    @classmethod 
    def get_client(cls) -> AsyncIOMotorClient:
        if cls._client is None:
            raise RuntimeError("MongoDB not connected. Call MongoDb.connect() first.")
        return cls._client
    
    @classmethod
    def get_db(cls) -> AsyncIOMotorDatabase:
        if cls._client is None:
            raise RuntimeError("MongoDB not connected. Call MongoDB.connect() first.")
        return cls._client[cls.DB_NAME]
    
    @classmethod
    def get_collection(cls, name: str) -> AsyncIOMotorCollection:
        return cls.get_db()[name]

    # Users
    @classmethod
    def users(cls) -> AsyncIOMotorCollection:
        return cls.get_collection("users")

    # GitHub
    @classmethod
    def github_installations(cls) -> AsyncIOMotorCollection:
        return cls.get_collection("github_installations")

    @classmethod
    def github_repositories(cls) -> AsyncIOMotorCollection:
        return cls.get_collection("github_repositories")

    @classmethod
    def webhook_deliveries(cls) -> AsyncIOMotorCollection:
        return cls.get_collection("webhook_deliveries")

    @classmethod
    def workflow_runs(cls) -> AsyncIOMotorCollection:
        return cls.get_collection("workflow_runs")

    # Slack
    @classmethod
    def slack_installations(cls) -> AsyncIOMotorCollection:
        return cls.get_collection("slack_installations")

    @classmethod
    def slack_threads(cls) -> AsyncIOMotorCollection:
        return cls.get_collection("slack_threads")

    @classmethod
    def slack_messages(cls) -> AsyncIOMotorCollection:
        return cls.get_collection("slack_messages")

    # Agents
    @classmethod
    def investigations(cls) -> AsyncIOMotorCollection:
        return cls.get_collection("investigations")

    @classmethod
    def agent_executions(cls) -> AsyncIOMotorCollection:
        return cls.get_collection("agent_executions")

    @classmethod
    def failure_fingerprints(cls) -> AsyncIOMotorCollection:
        return cls.get_collection("failure_fingerprints")

    @classmethod
    async def ping(cls) -> bool:
        try:
            await cls.get_client().admin.command("ping")
            return True
        except Exception:
            logger.exception("MongoDB ping failed")
            return False
        


    
