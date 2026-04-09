import sys
import logging
from mcp.server.fastmcp import FastMCP
import httpx
import os
from dotenv import load_dotenv

load_dotenv()

mcp = FastMCP("SonarQube Server")

SONAR_URL = os.environ.get("SONARQUBE_URL", "http://localhost:9000")
SONAR_TOKEN = os.environ.get("SONARQUBE_TOKEN", "")

@mcp.tool()
async def get_rule_details(rule_key: str) -> str:
    """Fetch complete documentation and details for a SonarQube rule Key."""
    try:
        auth = (SONAR_TOKEN, "") if SONAR_TOKEN else None
        
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{SONAR_URL}/api/rules/show",
                params={"key": rule_key},
                auth=auth,
            )
            
            if resp.status_code != 200:
                return f"Error fetching rule: HTTP {resp.status_code}"
                
            data = resp.json()
            rule = data.get("rule", {})
            html_desc = rule.get("htmlDesc", "No description available.")
            
            # Very basic cleanup
            html_desc = html_desc.replace("<p>", "").replace("</p>", "\n").replace("<br>", "\n")
            
            return f"Rule: {rule.get('name', rule_key)}\nDetails:\n{html_desc}"
    except Exception as e:
        return f"Error retrieving SonarQube rule: {str(e)}"

@mcp.tool()
async def search_issues(project_key: str, severity: str = "MAJOR") -> str:
    """Fetch open issues from SonarQube for a given project."""
    try:
        auth = (SONAR_TOKEN, "") if SONAR_TOKEN else None
        
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{SONAR_URL}/api/issues/search",
                params={
                    "componentKeys": project_key,
                    "statuses": "OPEN",
                    "severities": severity,
                    "ps": 20 # max 20 for brief search
                },
                auth=auth,
            )
            
            if resp.status_code != 200:
                return f"Error fetching issues: HTTP {resp.status_code}"
                
            data = resp.json()
            issues = data.get("issues", [])
            
            if not issues:
                return "No open issues found."
                
            summary = []
            for issue in issues:
                component = issue.get("component", "")
                rule = issue.get('rule', '')
                line = issue.get('line', 'N/A')
                message = issue.get('message', '')
                summary.append(f"- {component}:{line} [{rule}] -> {message}")
                
            return "\n".join(summary)
    except Exception as e:
        return f"Error searching SonarQube issues: {str(e)}"

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    mcp.run()
