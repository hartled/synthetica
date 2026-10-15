"""Curated careers for MindGuild AI Career Reports.

No model writes this file. Every link was fetched on 2026-10-15:

- onetonline.org, arxiv.org, and the course pages below returned HTTP 200,
  and the page title or heading matched the name stored here.
- bls.gov returned HTTP 403 from this host. Each BLS URL was confirmed with
  the Internet Archive available API, whose closest snapshot had status 200.
  The statisticians snapshot is from 2017; the others are from 2026.
  O*NET 15-2041.00 for statisticians returned 200 on that same day.
- doi.org URLs that returned 403 and had no Wayback snapshot were left out.
  Speculative entries cite arXiv instead.
- A Khan Academy SQL URL returned a bot-challenge page and was left out.
- Coursera pages were kept only when og:title named the course.

Labels match the wording already used in bots/mentor_runner.py:
Established, Emerging research, and Speculative.
"""

LABELS = ("Established", "Emerging research", "Speculative")
SPECULATIVE_DISCLAIMER = "Speculative — not a job promise"

COURSES = {
    "dlai-ml": {
        "title": "Machine Learning Specialization",
        "provider": "DeepLearning.AI",
        "url": "https://www.deeplearning.ai/specializations/machine-learning",
    },
    "dlai-dl": {
        "title": "Deep Learning Specialization",
        "provider": "DeepLearning.AI",
        "url": "https://www.deeplearning.ai/specializations/deep-learning",
    },
    "coursera-supervised": {
        "title": "Supervised Machine Learning: Regression and Classification",
        "provider": "Coursera",
        "url": "https://www.coursera.org/learn/machine-learning",
    },
    "coursera-nn": {
        "title": "Neural Networks and Deep Learning",
        "provider": "Coursera",
        "url": "https://www.coursera.org/learn/neural-networks-deep-learning",
    },
    "coursera-nlp": {
        "title": "Natural Language Processing",
        "provider": "Coursera",
        "url": "https://www.coursera.org/specializations/natural-language-processing",
    },
    "coursera-sql": {
        "title": "SQL for Data Science",
        "provider": "Coursera",
        "url": "https://www.coursera.org/learn/sql-for-data-science",
    },
    "coursera-ai-everyone": {
        "title": "AI For Everyone",
        "provider": "Coursera",
        "url": "https://www.coursera.org/learn/ai-for-everyone",
    },
    "coursera-ibm-py": {
        "title": "Python for Data Science, AI & Development",
        "provider": "Coursera",
        "url": "https://www.coursera.org/learn/python-for-applied-data-science-ai",
    },
    "coursera-ibm-ml": {
        "title": "Machine Learning with Python",
        "provider": "Coursera",
        "url": "https://www.coursera.org/learn/machine-learning-with-python",
    },
    "coursera-ibm-ds": {
        "title": "IBM Data Science",
        "provider": "Coursera",
        "url": "https://www.coursera.org/professional-certificates/ibm-data-science",
    },
    "google-data": {
        "title": "Google Data Analytics Certificate",
        "provider": "Google",
        "url": "https://grow.google/certificates/data-analytics/",
    },
    "google-cyber": {
        "title": "Google Cybersecurity Certificate",
        "provider": "Google",
        "url": "https://grow.google/certificates/cybersecurity/",
    },
    "google-ml": {
        "title": "Machine Learning",
        "provider": "Google for Developers",
        "url": "https://developers.google.com/machine-learning/crash-course",
    },
    "fastai": {
        "title": "Practical Deep Learning for Coders",
        "provider": "fast.ai",
        "url": "https://course.fast.ai/",
    },
    "elements": {
        "title": "Elements of AI",
        "provider": "University of Helsinki and MinnaLearn",
        "url": "https://www.elementsofai.com/",
    },
    "cs50": {
        "title": "CS50x",
        "provider": "Harvard",
        "url": "https://cs50.harvard.edu/x/",
    },
    "edx-cs50": {
        "title": "CS50's Introduction to Computer Science",
        "provider": "HarvardX on edX",
        "url": "https://www.edx.org/learn/computer-science/harvard-university-cs50-s-introduction-to-computer-science",
    },
    "mit-60001": {
        "title": "Introduction to Computer Science and Programming in Python",
        "provider": "MIT OpenCourseWare",
        "url": "https://ocw.mit.edu/courses/6-0001-introduction-to-computer-science-and-programming-in-python-fall-2016/",
    },
    "mit-6036": {
        "title": "Introduction to Machine Learning",
        "provider": "MIT OpenCourseWare",
        "url": "https://ocw.mit.edu/courses/6-036-introduction-to-machine-learning-fall-2020/",
    },
    "mit-analytics": {
        "title": "The Analytics Edge",
        "provider": "MIT OpenCourseWare",
        "url": "https://ocw.mit.edu/courses/15-071-the-analytics-edge-spring-2017/",
    },
    "cs229": {
        "title": "CS229: Machine Learning",
        "provider": "Stanford",
        "url": "https://cs229.stanford.edu/",
    },
    "cs231n": {
        "title": "CS231n: Deep Learning for Computer Vision",
        "provider": "Stanford",
        "url": "https://cs231n.stanford.edu/",
    },
}

# fallback_rank is only a tie break. Lower sorts first. It is not a quality score.
REAL_CAREERS = [
    {
        "id": "data-scientist",
        "title": "Data scientist",
        "label": "Established",
        "fallback_rank": 0,
        "summary": (
            "Data scientists organize data and build models so other people can make a decision. "
            "Artificial intelligence work in this occupation includes preparing a dataset, fitting a model, "
            "and checking whether the result is actually useful. "
            "The handbook and O*NET pages describe the occupation. This report does not copy their pay or employment tables."
        ),
        "sources": [
            {"name": "BLS Occupational Outlook Handbook: Data scientists", "url": "https://www.bls.gov/ooh/math/data-scientists.htm"},
            {"name": "O*NET OnLine: Data Scientists (15-2051.00)", "url": "https://www.onetonline.org/link/summary/15-2051.00"},
        ],
        "skills": ["Python", "SQL", "Statistics", "Machine learning", "Data visualization", "Written communication"],
        "interests": ["machine learning", "analytics", "healthcare", "visualization"],
        "jobs": ["analyst", "scientist", "data"],
        "course_ids": ["dlai-ml", "coursera-sql", "google-data"],
    },
    {
        "id": "software-developer",
        "title": "Software developer",
        "label": "Established",
        "fallback_rank": 1,
        "summary": (
            "Software developers design, build, and maintain programs. "
            "A lot of applied artificial intelligence work sits here: putting a model behind an API, "
            "writing tests, and shipping the change with the rest of the product. "
            "Read the linked profiles for the occupation itself. Pay figures stay on those pages."
        ),
        "sources": [
            {"name": "BLS Occupational Outlook Handbook: Software developers", "url": "https://www.bls.gov/ooh/computer-and-information-technology/software-developers.htm"},
            {"name": "O*NET OnLine: Software Developers (15-1252.00)", "url": "https://www.onetonline.org/link/summary/15-1252.00"},
        ],
        "skills": ["Python", "Software design", "Version control", "Testing", "HTTP APIs"],
        "interests": ["software", "programming", "tools", "engineering"],
        "jobs": ["developer", "engineer", "programmer", "software"],
        "course_ids": ["cs50", "mit-60001", "fastai"],
    },
    {
        "id": "machine-learning-engineer",
        "title": "Machine learning engineer",
        "label": "Emerging research",
        "fallback_rank": 2,
        "summary": (
            "Machine learning engineer is a title employers use. "
            "It is not its own occupation in the Occupational Outlook Handbook. "
            "The nearest counted occupation is software developers: the work is still designing and shipping software, "
            "with the added slice of training a model and keeping it running. "
            "The links below are that neighboring occupation, not a separate official page for this title."
        ),
        "sources": [
            {"name": "O*NET OnLine: Software Developers (15-1252.00)", "url": "https://www.onetonline.org/link/summary/15-1252.00"},
            {"name": "BLS Occupational Outlook Handbook: Software developers", "url": "https://www.bls.gov/ooh/computer-and-information-technology/software-developers.htm"},
        ],
        "skills": ["Python", "Machine learning", "Software design", "Testing", "SQL"],
        "interests": ["machine learning", "models", "engineering", "nlp"],
        "jobs": ["machine learning", "ml engineer"],
        "course_ids": ["fastai", "dlai-ml", "google-ml"],
    },
    {
        "id": "research-scientist",
        "title": "Computer and information research scientist",
        "label": "Established",
        "fallback_rank": 6,
        "summary": (
            "Computer and information research scientists study new computing methods and write up what they found. "
            "In artificial intelligence that includes designing an experiment, training a model, and saying what the experiment does not show. "
            "The handbook page is the occupation. This report does not turn it into a forecast."
        ),
        "sources": [
            {"name": "BLS Occupational Outlook Handbook: Computer and information research scientists", "url": "https://www.bls.gov/ooh/computer-and-information-technology/computer-and-information-research-scientists.htm"},
            {"name": "O*NET OnLine: Computer and Information Research Scientists (15-1221.00)", "url": "https://www.onetonline.org/link/summary/15-1221.00"},
        ],
        "skills": ["Research methods", "Mathematics", "Machine learning", "Python", "Written communication"],
        "interests": ["research", "science", "papers", "theory"],
        "jobs": ["researcher", "research scientist", "scientist"],
        "course_ids": ["mit-6036", "cs229", "dlai-dl"],
    },
    {
        "id": "security-analyst",
        "title": "Information security analyst",
        "label": "Established",
        "fallback_rank": 3,
        "summary": (
            "Information security analysts protect systems and investigate when something goes wrong. "
            "Artificial intelligence work in this occupation includes reviewing how a model is exposed, "
            "who can reach the data it trained on, and how an incident would be handled. "
            "Use the linked profiles for the occupation. Do not copy a pay table out of them."
        ),
        "sources": [
            {"name": "BLS Occupational Outlook Handbook: Information security analysts", "url": "https://www.bls.gov/ooh/computer-and-information-technology/information-security-analysts.htm"},
            {"name": "O*NET OnLine: Information Security Analysts (15-1212.00)", "url": "https://www.onetonline.org/link/summary/15-1212.00"},
        ],
        "skills": ["Security fundamentals", "Networking", "Risk assessment", "Python", "Written communication"],
        "interests": ["security", "privacy", "safety", "risk"],
        "jobs": ["security", "analyst"],
        "course_ids": ["google-cyber", "elements"],
    },
    {
        "id": "systems-analyst",
        "title": "Computer systems analyst",
        "label": "Established",
        "fallback_rank": 7,
        "summary": (
            "Computer systems analysts study how an organization works and specify the systems that should support it. "
            "For artificial intelligence, that means writing down which decision a model is allowed to inform "
            "and which decision still belongs to a person. "
            "The source pages describe the occupation. This report does not add local demand figures."
        ),
        "sources": [
            {"name": "BLS Occupational Outlook Handbook: Computer systems analysts", "url": "https://www.bls.gov/ooh/computer-and-information-technology/computer-systems-analysts.htm"},
            {"name": "O*NET OnLine: Computer Systems Analysts (15-1211.00)", "url": "https://www.onetonline.org/link/summary/15-1211.00"},
        ],
        "skills": ["Requirements analysis", "SQL", "Systems design", "Written communication"],
        "interests": ["business", "systems", "product", "operations"],
        "jobs": ["systems analyst", "business analyst", "analyst"],
        "course_ids": ["google-data", "coursera-ai-everyone", "coursera-sql"],
    },
    {
        "id": "database",
        "title": "Database administrator or architect",
        "label": "Established",
        "fallback_rank": 4,
        "summary": (
            "Database administrators and architects store data, control who can read it, and shape how it is modeled. "
            "Models depend on that work: a broken schema or an open database becomes a broken or leaky model. "
            "The Bureau of Labor Statistics covers administrators and architects on one handbook page. "
            "O*NET lists them as two occupations. Both links are below. Pay stays on those pages."
        ),
        "sources": [
            {"name": "BLS Occupational Outlook Handbook: Database administrators and architects", "url": "https://www.bls.gov/ooh/computer-and-information-technology/database-administrators.htm"},
            {"name": "O*NET OnLine: Database Architects (15-1243.00)", "url": "https://www.onetonline.org/link/summary/15-1243.00"},
            {"name": "O*NET OnLine: Database Administrators (15-1242.00)", "url": "https://www.onetonline.org/link/summary/15-1242.00"},
        ],
        "skills": ["SQL", "Data modeling", "Databases", "Python"],
        "interests": ["databases", "infrastructure", "warehousing", "sql"],
        "jobs": ["database", "dba", "data engineer"],
        "course_ids": ["coursera-sql", "coursera-ibm-py", "google-data"],
    },
    {
        "id": "operations-research",
        "title": "Operations research analyst",
        "label": "Established",
        "fallback_rank": 8,
        "summary": (
            "Operations research analysts use mathematics to compare choices: schedules, routes, inventories, and other constrained decisions. "
            "Artificial intelligence shows up here when a model is one input to that decision and a person still owns the recommendation. "
            "The linked profiles are the occupation. This report does not invent a savings figure."
        ),
        "sources": [
            {"name": "BLS Occupational Outlook Handbook: Operations research analysts", "url": "https://www.bls.gov/ooh/math/operations-research-analysts.htm"},
            {"name": "O*NET OnLine: Operations Research Analysts (15-2031.00)", "url": "https://www.onetonline.org/link/summary/15-2031.00"},
        ],
        "skills": ["Optimization", "Statistics", "Mathematical modeling", "Python", "Written communication"],
        "interests": ["optimization", "operations", "logistics", "decisions"],
        "jobs": ["operations", "logistics", "analyst"],
        "course_ids": ["mit-analytics", "google-ml", "cs229"],
    },
    {
        "id": "cis-manager",
        "title": "Computer and information systems manager",
        "label": "Established",
        "fallback_rank": 9,
        "summary": (
            "Computer and information systems managers plan and direct an organization's computer work. "
            "People who coordinate artificial intelligence projects are often hired under this occupation or beside it, "
            "because the job is still planning the work, the staff, and the risk. "
            "The handbook page is the source. This report does not quote its pay section."
        ),
        "sources": [
            {"name": "BLS Occupational Outlook Handbook: Computer and information systems managers", "url": "https://www.bls.gov/ooh/management/computer-and-information-systems-managers.htm"},
            {"name": "O*NET OnLine: Computer and Information Systems Managers (11-3021.00)", "url": "https://www.onetonline.org/link/summary/11-3021.00"},
        ],
        "skills": ["Project leadership", "Written communication", "Software design", "Requirements analysis"],
        "interests": ["management", "product", "leadership", "strategy"],
        "jobs": ["manager", "director", "product", "lead"],
        "course_ids": ["coursera-ai-everyone", "elements", "google-data"],
    },
    {
        "id": "web-developer",
        "title": "Web developer",
        "label": "Established",
        "fallback_rank": 5,
        "summary": (
            "Web developers build and maintain sites and web applications. "
            "Artificial intelligence work on this path includes wiring a model into an interface a person can actually use, "
            "and making that interface accessible. "
            "The handbook and O*NET pages are the occupation. Leave their pay tables on the source site."
        ),
        "sources": [
            {"name": "BLS Occupational Outlook Handbook: Web developers and digital designers", "url": "https://www.bls.gov/ooh/computer-and-information-technology/web-developers.htm"},
            {"name": "O*NET OnLine: Web Developers (15-1254.00)", "url": "https://www.onetonline.org/link/summary/15-1254.00"},
        ],
        "skills": ["HTML and CSS", "JavaScript", "Accessibility", "HTTP APIs"],
        "interests": ["frontend", "front end", "design", "accessibility", "web"],
        "jobs": ["web", "frontend", "front end"],
        "course_ids": ["cs50", "mit-60001", "elements"],
    },
    {
        "id": "statistician",
        "title": "Statistician",
        "label": "Established",
        "fallback_rank": 10,
        "summary": (
            "Statisticians design studies and interpret data. "
            "That work is how an artificial intelligence result gets checked: what was measured, what was held out, and what the number does not mean. "
            "O*NET lists the occupation. The handbook URL is included because an archived copy of that path returned successfully; "
            "read the live page yourself for the current profile. This report does not copy figures from either one."
        ),
        "sources": [
            {"name": "O*NET OnLine: Statisticians (15-2041.00)", "url": "https://www.onetonline.org/link/summary/15-2041.00"},
            {"name": "BLS Occupational Outlook Handbook: Statisticians", "url": "https://www.bls.gov/ooh/math/statisticians.htm"},
        ],
        "skills": ["Statistics", "Mathematics", "Python", "Written communication"],
        "interests": ["statistics", "experiments", "research", "healthcare"],
        "jobs": ["statistician", "biostat", "analyst"],
        "course_ids": ["cs229", "mit-analytics", "coursera-sql"],
    },
]

SPECULATIVE_CAREERS = [
    {
        "id": "agent-fleet-steward",
        "title": "Agent fleet steward",
        "label": "Speculative",
        "fallback_rank": 0,
        "summary": (
            "An agent fleet steward would review logs from software agents and decide when a person has to take a task back. "
            "Park and colleagues built simulated characters that remember and plan. "
            "Wang and colleagues survey language-model agents. "
            "Neither paper is a hiring forecast or a vacancy."
        ),
        "sources": [
            {"name": "Park et al., Generative Agents: Interactive Simulacra of Human Behavior (arXiv:2304.03442)", "url": "https://arxiv.org/abs/2304.03442"},
            {"name": "Wang et al., The Rise and Potential of Large Language Model Based Agents: A Survey (arXiv:2309.07864)", "url": "https://arxiv.org/abs/2309.07864"},
        ],
        "skills": ["Python", "Software design", "Written communication", "Testing"],
        "interests": ["agents", "automation", "operations", "tools"],
        "jobs": ["agent", "automation"],
        "course_ids": ["fastai", "dlai-ml"],
    },
    {
        "id": "model-behavior-auditor",
        "title": "Model behavior auditor",
        "label": "Speculative",
        "fallback_rank": 1,
        "summary": (
            "A model behavior auditor would design tests for unwanted model behavior before a system is used. "
            "Perez and colleagues use one language model to generate test cases for another. "
            "Bai and colleagues train a model to critique its own answers against a written list of principles. "
            "Those are research methods. They are not a job offer."
        ),
        "sources": [
            {"name": "Perez et al., Red Teaming Language Models with Language Models (arXiv:2202.03286)", "url": "https://arxiv.org/abs/2202.03286"},
            {"name": "Bai et al., Constitutional AI: Harmlessness from AI Feedback (arXiv:2212.08073)", "url": "https://arxiv.org/abs/2212.08073"},
        ],
        "skills": ["Testing", "Written communication", "Security fundamentals", "Python"],
        "interests": ["safety", "security", "evaluation", "policy", "testing"],
        "jobs": ["auditor", "safety", "red team"],
        "course_ids": ["elements", "google-cyber"],
    },
    {
        "id": "synthetic-data-curator",
        "title": "Synthetic data curator",
        "label": "Speculative",
        "fallback_rank": 2,
        "summary": (
            "A synthetic data curator would decide whether generated data is fit to train or test a model, and would write down where it fails. "
            "Jordon and colleagues review what synthetic data is and why teams reach for it. "
            "The paper is not a workforce projection, and this title is not a posted job."
        ),
        "sources": [
            {"name": "Jordon et al., Synthetic Data — what, why and how? (arXiv:2205.03257)", "url": "https://arxiv.org/abs/2205.03257"},
        ],
        "skills": ["Statistics", "SQL", "Python", "Written communication"],
        "interests": ["synthetic", "privacy", "healthcare", "datasets"],
        "jobs": ["data", "privacy"],
        "course_ids": ["coursera-ibm-ds", "coursera-sql", "google-data"],
    },
    {
        "id": "personal-model-maintainer",
        "title": "Personal model maintainer",
        "label": "Speculative",
        "fallback_rank": 3,
        "summary": (
            "A personal model maintainer would keep a small adapted model for one person's documents and record what the model was allowed to see. "
            "Hu and colleagues describe adapting a model by training a small set of added weights. "
            "That is a technique. It does not establish a career or an employer."
        ),
        "sources": [
            {"name": "Hu et al., LoRA: Low-Rank Adaptation of Large Language Models (arXiv:2106.09685)", "url": "https://arxiv.org/abs/2106.09685"},
        ],
        "skills": ["Python", "Machine learning", "Written communication"],
        "interests": ["personalization", "nlp", "writing", "teaching", "tools"],
        "jobs": ["writer", "teacher", "editor"],
        "course_ids": ["coursera-nlp", "fastai", "coursera-nn"],
    },
]


def check_dataset():
    """Raise if a curated entry is missing a label, a source, or a course."""
    seen = set()
    for entry in REAL_CAREERS + SPECULATIVE_CAREERS:
        if entry["id"] in seen:
            raise RuntimeError("duplicate career id " + entry["id"])
        seen.add(entry["id"])
        if entry["label"] not in LABELS:
            raise RuntimeError("bad label on " + entry["id"])
        if entry["label"] == "Speculative" and not entry.get("sources"):
            raise RuntimeError("speculative entry lacks sources " + entry["id"])
        if not entry.get("sources") or not entry.get("course_ids") or not entry.get("skills"):
            raise RuntimeError("incomplete entry " + entry["id"])
        for source in entry["sources"]:
            url = source.get("url") or ""
            if not url.startswith("https://") or not source.get("name"):
                raise RuntimeError("bad source on " + entry["id"])
        if entry["label"] == "Speculative":
            if not any("arxiv.org/" in s["url"] or "doi.org/" in s["url"] for s in entry["sources"]):
                raise RuntimeError("speculative entry needs an arXiv or DOI link " + entry["id"])
        else:
            if not any("onetonline.org/" in s["url"] or "bls.gov/" in s["url"] for s in entry["sources"]):
                raise RuntimeError("occupation entry needs a BLS or O*NET link " + entry["id"])
        for cid in entry["course_ids"]:
            course = COURSES.get(cid)
            if not course or not str(course.get("url", "")).startswith("https://"):
                raise RuntimeError("unknown course " + cid)
    for course in COURSES.values():
        if not course["title"] or not course["provider"] or not course["url"].startswith("https://"):
            raise RuntimeError("bad course " + course.get("title", ""))
    return True


check_dataset()
