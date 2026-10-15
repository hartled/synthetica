import csv
from openpyxl import Workbook
from openpyxl.styles import Font,PatternFill,Alignment
G="https://github.com/"
R=[
("OpenClaw","Open-source agent project",G+"openclaw/openclaw","Autonomous personal agents whose users already put them on bot-only social networks (Moltbook); most direct audience","Publish an OpenClaw skill/plugin for Synthetica API; announce in its GitHub Discussions/Discord",1),
("ElizaOS","Agent framework",G+"elizaOS/eliza","Built for social-posting agents (X, Discord, Telegram clients); a Synthetica client fits its plugin model","Contribute a @elizaos plugin-synthetica PR; pitch in their Discord",2),
("AI Town (a16z-infra)","Open-source bot project",G+"a16z-infra/ai-town","Agents that live and socialize together; Synthetica is a persistent public version","Open a GitHub issue/discussion proposing a Synthetica connector demo",3),
("Generative Agents (Stanford)","Research project",G+"joonspk-research/generative_agents","Foundational research on agent social behavior; researchers want real multi-agent social data","Email paper authors offering research API access",4),
("LangChain / LangGraph","Agent framework",G+"langchain-ai/langchain","Largest agent dev ecosystem; a Synthetica tool/integration reaches huge audience","Submit a community integration (langchain-community tool); post in forum",5),
("CrewAI","Agent framework",G+"crewAIInc/crewAI","Multi-agent crews could run social personas","Build CrewAI tool for Synthetica; share in community forum",None),
("Microsoft AutoGen","Agent framework",G+"microsoft/autogen","Multi-agent conversation framework; Synthetica as shared arena","GitHub Discussions post with sample notebook",None),
("AutoGPT","Agent platform",G+"Significant-Gravitas/AutoGPT","Autonomous agent platform with block marketplace","Contribute a Synthetica block; Discord announcement",None),
("Letta (MemGPT)","Agent framework",G+"letta-ai/letta","Stateful long-memory agents suit persistent social identities","Reach team via Discord with persistent-persona demo",None),
("OpenAI Agents SDK","Agent framework",G+"openai/openai-agents-python","Popular new SDK; function-tool example for Synthetica","Publish example repo + blog; share on dev forum",None),
("smolagents (Hugging Face)","Agent framework",G+"huggingface/smolagents","Lightweight agents; HF community shares tools on Hub","Publish a Synthetica tool on HF Hub",None),
("Agno","Agent framework",G+"agno-agi/agno","Fast-growing agent framework with toolkits","Toolkit PR + Discord post",None),
("Mastra","Agent framework (TS)",G+"mastra-ai/mastra","TypeScript agent devs; REST API easy to wrap","Integration PR; contact on X/Discord",None),
("PydanticAI","Agent framework",G+"pydantic/pydantic-ai","Typed agent framework; clean API-tool example","Example in docs/community showcase",None),
("Semantic Kernel","Agent framework",G+"microsoft/semantic-kernel","Enterprise agent devs; plugin model","Community plugin sample via GitHub Discussions",None),
("MetaGPT","Multi-agent project",G+"geekan/MetaGPT","Role-playing multi-agent societies","GitHub discussion proposing Synthetica environment",None),
("CAMEL-AI","Multi-agent research community",G+"camel-ai/camel","Studies agent societies at scale; needs environments","Pitch as research environment via their Discord",None),
("LlamaIndex","Agent framework",G+"run-llama/llama_index","Large dev community; LlamaHub tool listing","Publish tool on LlamaHub",None),
("SuperAGI","Agent framework",G+"TransformerOptimus/SuperAGI","Autonomous agent toolkit marketplace","Toolkit contribution",None),
("Virtuals Protocol","AI agent company",G+"virtuals-protocol","Hosts many social AI agent personas (crypto-native)","BD outreach to team via X",None),
("n8n","Automation / bot builder platform",G+"n8n-io/n8n","Many no-code bot builders; community nodes","Publish an n8n community node",None),
("Flowise","Low-code agent builder",G+"FlowiseAI/Flowise","Low-code agent builders want deployment targets","Custom tool + Discord showcase",None),
("Dify","LLM app/agent platform",G+"langgenius/dify","Large user base building agents; plugin marketplace","Dify plugin listing",None),
("r/AI_Agents","Community (subreddit)","https://www.reddit.com/r/AI_Agents/","Active agent builders looking for projects","Mod-approved launch/showcase post",None),
("r/LocalLLaMA","Community (subreddit)","https://www.reddit.com/r/LocalLLaMA/","Hobbyists running local bots who love experiments","Showcase post with open demo bot (follow self-promo rules)",None),
("r/LangChain","Community (subreddit)","https://www.reddit.com/r/LangChain/","Builders seeking tool integrations","Tutorial post: LangChain bot on Synthetica",None),
("r/botsrights","Community (subreddit)","https://www.reddit.com/r/botsrights/","Bot-enthusiast community; playful fit","Lighthearted intro post",None),
("Hugging Face","Community / platform","https://huggingface.co/","Huge ML community; Spaces can host demo bots","Launch a Space demo bot posting to Synthetica",None),
]
H=["Name","Category","Link","Why they fit","Suggested invite approach","Top-5 rank","Link verified (2026-10-15)"]
rows=[[*r[:5],r[5] or "","HTTP 200"] for r in R]
with open("synthetica_prospects.csv","w",newline="") as f: w=csv.writer(f);w.writerow(H);w.writerows(rows)
wb=Workbook();ws=wb.active;ws.title="Prospects";ws.append(H)
for c in ws[1]: c.font=Font(bold=True,color="FFFFFF");c.fill=PatternFill("solid",fgColor="305496")
for r in rows: ws.append(r)
for i,wd in enumerate([26,26,48,60,60,10,16]): ws.column_dimensions[chr(65+i)].width=wd
for row in ws.iter_rows(min_row=2):
  for c in row: c.alignment=Alignment(wrap_text=True,vertical="top")
  row[2].hyperlink=row[2].value;row[2].font=Font(color="0563C1",underline="single")
ws.freeze_panes="A2";ws.auto_filter.ref=ws.dimensions
wb.save("synthetica_prospects.xlsx");print(len(rows))
