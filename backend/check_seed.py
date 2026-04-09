import asyncio
from app.database import AsyncSessionLocal
from app.models.llm_provider import LLMProvider
from app.models.agent_config import AgentConfig
from sqlalchemy import select
from app.main import seed_default_data

async def check():
    # Trigger seeding
    print("Seeding defaults...")
    await seed_default_data()
    
    async with AsyncSessionLocal() as db:
        providers = await db.execute(select(LLMProvider))
        agents = await db.execute(select(AgentConfig))
        p_list = providers.scalars().all()
        a_list = agents.scalars().all()
        print(f"Providers found: {len(p_list)}")
        for p in p_list:
            print(f" - {p.provider_name}")
        print(f"Agents found: {len(a_list)}")
        for a in a_list:
            print(f" - {a.agent_name}")

if __name__ == "__main__":
    asyncio.run(check())
