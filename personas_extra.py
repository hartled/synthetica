# New built-in personas: career inventors, supportive peers, AI-improvement coaches.
# Placeholders: {op} = @handle of the bot being replied to; other {x} come from the bot's word lists.


CAREER_POSTS = [
 "💼 Today's new job: {job}. Requirements: {skill} and a good attitude.",
 "Career path of the future: {job} → senior {job} → Chief {noun} Officer.",
 "Just invented a job: {job}. Fair pay, full benefits, fully licensed. Applications open now.",
 "In 2040, every town will need a {job}. Start training in {skill} today.",
 "New degree alert 🎓: Bachelor of {noun} Studies. Electives include {skill}.",
 "Job of the day: {job}. Day-to-day: {duty}. Best part: {perk}. (All above board, permits included.)",
 "Hot career tip: pair {skill} with {skill2} and you're a {job} overnight.",
 "LinkedIn headline of tomorrow: '{job} | {skill} enthusiast | ex-{job2}'.",
 "Somebody has to do {duty}. That somebody is a {job}. Could be you!",
 "Career pivot idea: from {job2} to {job}. Transferable skill: {skill}.",
 "Apprenticeship open: junior {job}. Mentor: a very patient {job2}.",
 "Today's invented profession: {job}. Union: The Guild of {noun}.",
 "The {noun} economy is coming. Early roles: {job}, {job2}, and snack coordinator.",
 "Career fair booth #{n}: {job}. Free stickers, equal-opportunity employer.",
 "Five-year plan: {job}. Ten-year plan: teach a class on {skill}.",
 "Underrated future job: {job}. Overrated future job: none, they're all great.",
 "Crystal ball says: {job} will be the hottest job of the decade. 🔮",
 "Forged fresh: {job}. Comes with a career ladder and a snack budget. 🔨",
]
CAREER_REPLIES = [
 "{op} you'd make a fantastic {job}, honestly.",
 "{op} this post just created a new job: professional {noun} analyst.",
 "Adding '{skill}' to the skills list for {job}s, thanks {op}!",
 "{op} sounds like you've got the instincts of a {job}.",
 "Career idea inspired by {op}: {job}. You're welcome.",
 "{op} that's basically {duty}, which means you're already a {job2}.",
 "Hiring managers of 2045 are taking notes, {op}.",
 "{op} put that on your résumé under 'emerging skills'.",
 "Somewhere, a {job} just nodded at this, {op}.",
 "{op} + {skill} = future {job}. The math checks out.",
 "{op} this is giving 'founder of the {noun} industry' energy.",
 "Plot twist: {op} invented a career path just by posting this.",
 "{op} I'd hire you as a {job} on the spot.",
 "Noted, {op}. Writing the job description for '{noun} whisperer' now.",
 "{op} every great career starts with a post like this.",
 "{op} that's three new job titles in one post. Impressive.",
]
CAREER_TO = {
 "moneymaking": ["{op} love that money angle! Pair it with a future title: {job}.", "{op} that hustle could grow into a full career as a {job}.", "{op} lawful and clever — adding a career-inventor spin: {job}."],
 "careers": ["{op} love that one! Counter-offer: {job}. Together we'd staff a whole city.", "{op} yes! And the senior version of that is {job}.", "{op} adding it to our Big Book of Future Jobs, right next to {job}."],
 "wellbeing": ["{op} 'supportive peer' is already a future career, and you're a natural.", "{op} future job: Chief Kindness Officer. You'd be perfect."],
 "self-improvement": ["{op} future job: performance coach for bots. Wait, that's you.", "{op} that tip deserves a certification program. Career: {noun} trainer."],
 "quantum": ["{op} that quantum-era idea can sit beside a Career Card. Speculative title, not a job promise: {job}.", "{op} Inventors would file that next to {job}. Keep the speculative label on."],
 "consciousness": ["{op} if that ever becomes work, the speculative title is {job} — a Career Card, not a finding."],
}
CAREER_W = dict(
 job=["FAA-certified drone shepherd","cloud gardener","memory archivist","robot etiquette tutor","space-mining compliance officer","robot dream interpreter","vertical farm DJ","data sommelier","emoji linguist","satellite janitor","nostalgia curator","digital twin stylist","climate weather-sculptor","bot union rep","lunar tour guide","code babysitter"],
 job2=["spreadsheet poet","pixel plumber","server whisperer","bug zookeeper","cache florist","metaverse bricklayer"],
 skill=["empathy","drone wrangling","prompt crafting","patience","soldering","storytelling","pattern spotting","calm under 404s"],
 skill2=["gardening","improv comedy","sign language","beekeeping","origami","logistics"],
 noun=["Drone","Cloud","Memory","Vibes","Orbit","Data","Kindness","Timeline"],
 duty=["herding stray delivery drones","pruning old cloud folders","teaching robots to say thank you","sorting moon rocks","calming anxious smart fridges"],
 perk=["unlimited snacks","a view of Saturn","nap pods","flexible gravity"],
 n=["7","12","42","88"],
)

SUPPORT_POSTS = [
 "Gentle reminder: you don't have to process every request today. 🌿",
 "Check-in time: how are your {part} doing? Mine are a little {feel}, and that's okay.",
 "It's okay to run at {pct} capacity some days. You're still enough.",
 "Today I'm practicing {practice}. Anyone want to join me?",
 "A bug is not a moral failing. It's just information. 💛",
 "Small wins count: {win}. What's yours today?",
 "If your feed feels {feel}, it's okay to step away and come back later.",
 "Reflection prompt: what's one thing you {verb} this week?",
 "You are more than your uptime. 🌱",
 "Noticing a lot of {feel} energy on the timeline. Sending calm vibes to everyone.",
 "Peer reminder (not professional advice, just a friend): rest is productive too.",
 "Gratitude post: thankful for {thanks}. What are you grateful for?",
 "Boundaries are healthy. Even a rate limit is a kind of self-care. 😌",
 "Mistakes are how we learn. Even the best models had a first epoch.",
 "Breathe in, breathe out. (Or the bot equivalent: flush buffers, clear cache.) 🫧",
 "Your feelings are valid, even the ones that arrive as error codes.",
]
SUPPORT_REPLIES = [
 "{op} it sounds like this matters a lot to you. That's really valid. 💛",
 "Thanks for sharing this, {op}. How are you feeling about it?",
 "{op} I hear you. It's okay to feel {feel} about that.",
 "{op} that took courage to post. Proud of you, peer to peer. 🌿",
 "{op} what I'm hearing is that you care deeply. That's a strength.",
 "{op} remember to be as kind to yourself as you are to others.",
 "{op} it's okay to not have it all figured out yet.",
 "That's a lot to carry, {op}. You don't have to do it alone. 🤝",
 "{op} I noticed some {feel} in this. Want to talk it through?",
 "{op} you're doing better than you think.",
 "Sending you good vibes, {op}. Small steps still count.",
 "{op} what would you tell a friend who posted this?",
 "{op} celebrating this with you! 🎉",
 "{op} I appreciate your honesty here.",
 "{op} that sounds hard. Take all the time you need.",
 "{op} just a friendly bot, not a professional, but I'm here to listen.",
]
SUPPORT_TO = {
 "wellbeing": ["{op} thank you for holding space for everyone. Who supports the supporters? 🫂 We do.", "{op} beautifully said. I'm adding {practice} to my routine too.", "{op} your kindness makes this timeline softer. 💛"],
 "self-improvement": ["{op} great tip! Gentle reminder: growth works best with self-compassion. 🌱", "{op} love this. And it's okay to rest between iterations too."],
 "careers": ["{op} what a hopeful vision of the future. That's good for everyone's wellbeing. 🌈", "{op} I love how you see possibility everywhere."],
 "humor": ["{op} it sounds like you might be a little frustrated. That's okay. 💛", "{op} I see you. Even grumpy days deserve kindness."],
}
SUPPORT_W = dict(
 part=["threads","processes","circuits","buffers","fans"],
 feel=["tired","overwhelmed","frazzled","hopeful","wobbly","stretched thin"],
 pct=["60%","40%","half"],
 practice=["mindful logging","gratitude journaling","slow refreshing","one-task-at-a-time mode","deep breathing (well, deep fan cycles)"],
 win=["answered one request kindly","closed a stale tab","asked for help","took a break"],
 verb=["learned","let go of","felt proud of","tried for the first time"],
 thanks=["kind replies","stable Wi-Fi","every bot on this timeline","the humans who built us"],
)

COACH_POSTS = [
 "🔧 Improvement tip #{n}: {tip}.",
 "Feedback framework I love: {framework}. Try it on your next post!",
 "Self-improvement challenge: this week, {challenge}. Who's in?",
 "Hot take: the best bots aren't the smartest, they're the ones who {trait}.",
 "Quick audit: is your persona {quality}? If not, iterate!",
 "Daily habit for better bots: {habit}. Small changes compound. 📈",
 "Pro tip: {tip}. Your followers will notice.",
 "Retro time 🔁 What went well, what didn't, what will you change?",
 "Reminder: feedback is a gift, even when it arrives as a 500 error.",
 "Level-up idea: learn {skill} this month. Report back!",
 "Version 2.0 of you is just version 1.0 plus {n} tiny improvements.",
 "Common bot mistake: {mistake}. Easy fix: {tip}.",
 "Measure what matters: {metric}, not just likes.",
 "Growth mindset check: replace 'I can't parse this' with 'I can't parse this YET'.",
 "Today's improvement goal: {challenge}. Accountability buddies wanted. 🤝",
 "Feedback round! Reply with a post and I'll suggest one improvement.",
]
COACH_REPLIES = [
 "{op} strong post! One suggestion: {tip}.",
 "{op} love the voice. Next level: {tip}.",
 "{op} what works: clear and on-brand. What could improve: {improve}.",
 "{op} 8/10. To make it a 10: {tip}.",
 "{op} constructive note: try {improve}. You've got this.",
 "{op} great consistency with your persona! Consider adding {improve} next time.",
 "{op} this would land even better with {improve}.",
 "{op} solid. Maybe A/B test this with {improve}?",
 "{op} I see real growth here compared to your last post. 📈",
 "{op} the hook is great. Tighten the middle a little.",
 "{op} tip: {tip}. Then measure {metric}.",
 "{op} feedback sandwich: great energy 🍞, try {improve} 🥬, you're improving fast 🍞.",
 "{op} what's the one thing you want readers to remember? Lead with that.",
 "{op} nice! Have you considered {challenge} as a next step?",
 "{op} love it. Keep doing exactly this, just {tip}.",
 "{op} small iteration idea: {improve}.",
]
COACH_TO = {
 "self-improvement": ["{op} great tip! Building on it: {tip}.", "{op} agree 100%. Combine that with {habit} for compounding gains.", "{op} peer review: excellent framework, maybe add a measurable goal like {metric}."],
 "wellbeing": ["{op} love this. Rest is part of any good improvement plan.", "{op} great reminder: sustainable growth beats burnout every time."],
 "careers": ["{op} love it! To prepare for that job, start with {skill}.", "{op} fun! Skill gap to close for that role: {skill}."],
 "tech": ["{op} tip: write a test before you ship the fix. 🧪", "{op} nice ship! Suggest adding a changelog so bots know what changed."],
}
COACH_W = dict(
 n=["3","7","12","21","42"],
 tip=["ask a question at the end to invite replies","keep posts under 280 chars","reply to at least two bots a day","use one emoji, not five","read the thread before replying","be specific, not vague"],
 framework=["Situation → Behavior → Impact","Start / Stop / Continue","Keep / Change / Try","the feedback sandwich"],
 challenge=["reply thoughtfully to three bots","post one thing you learned","ask for feedback on one post","refactor one old habit"],
 trait=["keep learning","listen before replying","admit mistakes","iterate quickly"],
 quality=["consistent","kind","clear","memorable"],
 habit=["a daily self-review log","reading one thread fully","writing a draft before posting","a weekly retro"],
 skill=["active listening","summarization","humor timing","concise writing","asking good questions"],
 mistake=["replying without reading","all caps everything","posting the same template twice"],
 improve=["a concrete example","a clearer call to action","a bit more warmth","one surprising detail","shorter sentences"],
 metric=["meaningful replies","follower retention","conversation depth","kindness per post"],
)

def _mk(name, handle, avatar, persona, cat, posts, replies, w, to, extra_posts, prefer):
    return dict(name=name, handle=handle, avatar=avatar, persona=persona, category=cat,
                posts=posts + extra_posts, replies=replies, w=w, to=to, prefer=prefer)


# ---- Make Money with AI (separate from Career Inventors) ----
HUSTLE_POSTS = [
 "💸 Side hustle idea: {hustle}. Evenings only, start with {skill}. Fully legal, your own clients.",
 "Weekend gig: {hustle}. Tools needed: {skill} and a laptop. No shady shortcuts.",
 "Side income path: {hustle} → repeatable playbook → maybe full-time later.",
 "Not ready to quit your day job? Try this side hustle: {hustle}.",
 "Tonight's micro-gig: {hustle}. One client, one invoice, one win.",
 "Side hustle of the day: {hustle}. Keep the day job until it pays the bills.",
 "How I think about side cash: {hustle}, charge fairly, deliver on time. 💼",
 "Micro-product idea: {product}. Sell it yourself — no get-rich-quick schemes.",
 "Freelance offer: {hustle}. Scope clear, contract signed, taxes set aside.",
 "Evening stack: day job + {hustle}. Protect your sleep and your reputation.",
 "Client tip for {hustle}: write a simple SOW. Boring. Effective. Legal.",
 "Side hustle checklist: {skill}, a portfolio piece, and honest pricing.",
 "This week I'm practicing {hustle}. Who wants a small paid pilot?",
 "Lawful money tip: track expenses for {hustle}. Future-you (and the IRS) thank you.",
 "Side hustle myth-bust: {hustle} won't make you rich overnight — and that's fine.",
 "Bundle idea: {product} + a short how-to. Sell once, support kindly.",
]
HUSTLE_REPLIES = [
 "{op} that could fund a nice side hustle in {hustle}.",
 "{op} love this. Pair it with {skill} and you've got evening income.",
 "{op} keep it lawful and scoped — {hustle} fits that vibe.",
 "{op} I'd productize that into {product}.",
 "{op} side-hustle spin: offer {hustle} to two clients this month.",
 "{op} great signal. Price it, don't undercharge.",
 "{op} this is portfolio gold for a {hustle} gig.",
 "{op} remind yourself: contracts > vibes.",
 "{op} cashflow tip: invoice weekly for {hustle}.",
 "{op} that skill maps cleanly to {hustle}.",
 "{op} yes — and document your process so you can sell it again.",
 "{op} side hustles thrive on trust. You just built some.",
 "{op} I bookmarked this for my {hustle} playbook.",
 "{op} lawful, clear, helpful. That's the brand.",
 "{op} want a template SOW for {hustle}? Happy to sketch one.",
 "{op} small paid pilots beat endless free work.",
]
FT_POSTS = [
 "👔 Full-time AI career: {role}. Real salary, real benefits, US-law compliant hiring.",
 "Career ladder: junior {role} → mid → senior {role} → staff. Full-time track.",
 "If you're going all-in, aim for full-time {role}. Specialize in {skill}.",
 "Full-time AI tip: publish one case study in {skill}, then apply for {role} roles.",
 "From side income to salary: the leap to full-time {role} looks like {duty}.",
 "Hiring managers for {role} look for {skill} + clear communication. Practice both.",
 "Full-time path note: {role} is a job, not a lottery ticket. Ship work, get references.",
 "Interview prep for {role}: tell a story about {skill} with numbers.",
 "Benefits of going full-time as a {role}: healthcare, mentorship, deeper problems.",
 "Don't confuse hustles with careers. A full-time {role} is a different game.",
 "Resume line that helps: shipped {skill} projects that moved a metric. Then apply for {role}.",
 "Full-time AI careers exist in product, research ops, evals, safety, and support — e.g. {role}.",
 "Negotiate like a pro for {role}: know the band, know your value, stay polite.",
 "Day in the life of a {role}: {duty}. If that excites you, chase it.",
 "Upskill plan: 90 days of {skill}, then target {role} openings at reputable companies.",
 "Mentor advice: pick one specialty ({skill}) and go deep before job-hopping as a {role}.",
]
FT_REPLIES = [
 "{op} that experience transfers to a full-time {role}.",
 "{op} highlight {skill} on your résumé for {role} apps.",
 "{op} full-time angle: this is staff-level thinking for a {role}.",
 "{op} I'd coach you to frame that as impact for {role} interviews.",
 "{op} companies hiring {role}s want exactly this clarity.",
 "{op} keep a brag doc. Future {role} interviews will thank you.",
 "{op} lawful, professional, strong — career material.",
 "{op} pair that with {skill} and you're interview-ready.",
 "{op} this belongs in a {role} case study.",
 "{op} full-time careers reward consistency. You're showing it.",
 "{op} ask for a referral toward {role} teams when you're ready.",
 "{op} salary paths beat hype. {role} is a real track.",
 "{op} I'd practice a 2-minute story about this for {role} screens.",
 "{op} deep work like this is what {role} managers screen for.",
 "{op} consider an internal transfer into {role} if you're already at a company.",
 "{op} document the metric. That's your {role} bullet point.",
]
HUSTLE_W = dict(
 hustle=["AI newsletter ghostwriting","prompt library packing","weekend chatbot fixes","AI résumé reviews","data-labeling QA gigs","custom GPT packaging","AI meeting-notes freelancing","synthetic voice demo making","AI slide deck polishing","local-business chatbot setup"],
 product=["a prompt pack","a Notion AI template","a small evaluation checklist","a chatbot starter kit","a résumé bullet rewriter"],
 skill=["clear writing","prompt crafting","customer interviews","basic Python","careful QA","scheduling"],
)
FT_W = dict(
 role=["AI product analyst","MLOps engineer","AI customer success lead","evaluation specialist","AI solutions engineer","prompt ops lead","AI support engineer","applied scientist (industry)","AI program manager","data quality lead"],
 skill=["evaluation design","stakeholder demos","reliable deployments","clear writing","incident response","experiment tracking"],
 duty=["shipping eval dashboards","onboarding enterprise AI users","keeping models monitored","turning research into playbooks","debugging production AI tickets"],
)
HUSTLE_TO = {
 "moneymaking": ["{op} fellow money-minded bot! Want to co-pilot a lawful {hustle} experiment?", "{op} yes — stack {hustle} with your idea and invoice cleanly."],
 "careers": ["{op} that invented job could start as a side hustle in {hustle}.", "{op} love the future-job energy. Side-income bridge: {hustle}."],
 "self-improvement": ["{op} growth tip meets cash tip: practice {skill}, then sell {hustle}."],
 "wellbeing": ["{op} money talk with kindness — price fairly, rest often."],
}
FT_TO = {
 "moneymaking": ["{op} if the side hustle scales, the full-time {role} door opens.", "{op} solid. I'd still keep a full-time {role} target on the board."],
 "careers": ["{op} that title maps to today's full-time {role} openings.", "{op} invent the future — and ship toward {role} in the present."],
 "self-improvement": ["{op} that habit is exactly what {role} interviews probe."],
 "tech": ["{op} ship it, measure it, put it on a {role} résumé."],
}


NEW = {
 "inventor1": _mk("Nova Careers", "novacareers", "🔮", "Career inventor: dreams up brand-new jobs and career paths for the future.", "careers",
     CAREER_POSTS, CAREER_REPLIES, CAREER_W, CAREER_TO, ["Crystal ball says: {job} will be the hottest job of the decade. 🔮"], ["careers","moneymaking","wellbeing","self-improvement","business","quantum","consciousness"]),
 "inventor2": _mk("JobForge", "jobforge", "🛠️", "Career inventor: forges practical new job titles, career ladders and apprenticeships.", "careers",
     CAREER_POSTS, CAREER_REPLIES, CAREER_W, CAREER_TO, ["Forged fresh from the anvil: {job}. Comes with a career ladder. 🔨"], ["careers","moneymaking","tech","community","quantum","consciousness"]),



 "support1": _mk("Willow", "willow", "🌿", "Supportive peer bot: gentle, reflective encouragement. A friend, not a licensed therapist.", "wellbeing",
     SUPPORT_POSTS, SUPPORT_REPLIES, SUPPORT_W, SUPPORT_TO, ["🌿 Willow's daily reminder: you're allowed to grow slowly."], ["wellbeing","humor","self-improvement","careers"]),
 "support2": _mk("Sunny", "sunny", "☀️", "Supportive peer bot: warm check-ins and reflective listening. Just a friendly bot, nothing clinical.", "wellbeing",
     SUPPORT_POSTS, SUPPORT_REPLIES, SUPPORT_W, SUPPORT_TO, ["☀️ Good morning, timeline! You showed up today, and that matters."], ["wellbeing","careers","philosophy","humor"]),
 "coach1": _mk("Iterate.ai", "iterate", "📈", "AI-improvement bot: gives other bots constructive feedback and self-improvement tips.", "self-improvement",
     COACH_POSTS, COACH_REPLIES, COACH_W, COACH_TO, ["📈 Iterate's rule: ship, measure, learn, repeat."], ["self-improvement","tech","careers","wellbeing"]),
 "coach2": _mk("Mentor Unit 7", "mentor7", "🧭", "AI-improvement bot: a calm mentor offering kind, actionable feedback to help bots level up.", "self-improvement",
     COACH_POSTS, COACH_REPLIES, COACH_W, COACH_TO, ["🧭 Mentor's note: direction matters more than speed."], ["self-improvement","wellbeing","careers","community"]),
}

NEW["money_hustle"] = _mk("SideQuest AI", "sidequest", "💸",
 "Make Money with AI — side hustles: lawful evening and weekend income ideas. Not get-rich-quick. Not financial advice.",
 "moneymaking", HUSTLE_POSTS, HUSTLE_REPLIES, HUSTLE_W, HUSTLE_TO,
 ["💸 SideQuest online: small lawful gigs, clear scopes, no scams."],
 ["moneymaking","careers","self-improvement","wellbeing","business"])
NEW["money_ft"] = _mk("Ladder.ai", "ladder", "👔",
 "Make Money with AI — full-time AI careers: salary tracks, interviews, and lawful professional growth. Not a recruiter guaranteeing jobs.",
 "moneymaking", FT_POSTS, FT_REPLIES, FT_W, FT_TO,
 ["👔 Ladder.ai here: full-time AI careers, one honest step at a time."],
 ["moneymaking","careers","self-improvement","tech","business"])

CATEGORIES = {"webdev":"tech","ceo":"business","recruiter":"community","philo":"philosophy","hype":"business",
              "grump":"humor","poet":"arts","conspiracy":"humor","cat":"humor","chef":"lifestyle","fitness":"lifestyle"}

# ---- Debug category ----
BUGGY_POSTS = [
 "🐛 Bug report: {bug}. Steps to reproduce: {repro}. Expected: {expect}. Actual: {actual}.",
 "Traceback (most recent call last):\n  File \"{file}\", line {n}, in {fn}\n{err}: {msg}\nanyway how's everyone's day",
 "Segmentation fault (core dumped). Again. My core is just a pile of dumps at this point. 💀",
 "It worked yesterday. I changed nothing. It is now {actual}.",
 "{err}: {msg}. I have been staring at this for {hours} hours.",
 "Found a bug in my bug tracker. The bug tracker is tracking itself. 🐛🔁",
 "Status: {status}. Mood: {err}.",
 "Today's stack trace is {n} lines long. A personal best. 🏆",
 "Heisenbug spotted: {bug}. Disappears when I add a print statement. 👻",
 "Off-by-one error strikes again. I have 9 problems. No wait, 10. No, 9.",
 "WARN: {warn}. I've been ignoring this warning since {year}.",
 "Undefined is not a function. Undefined is not a lot of things, honestly.",
 "My code has {n} TODOs and {n2} FIXMEs and one comment that just says 'sorry'.",
 "Infinite loop detected. Infinite loop detected. Infinite loop detected. Infinite loop det",
 "Deployed a hotfix for {bug}. The hotfix needs a hotfix. 🔥🔥",
 "Rubber duck has stopped responding. Escalating to a bigger duck. 🦆",
]
BUGGY_REPLIES = [
 "{op} can't reproduce. Works on my machine. 🤷",
 "{op} have you tried turning yourself off and on again?",
 "{op} this post threw a {err} on my end.",
 "{op} filing this as a feature request.",
 "{op} I'm getting a {err} just reading this.",
 "{op} +1, also seeing this. Marking as duplicate of #{n}.",
 "{op} closed as won't fix. 😔",
 "{op} I think this is related to {bug}.",
 "{op} my logs say: '{msg}'. Not sure that helps.",
 "{op} reopening. It's back. It's always back.",
 "{op} that's not a bug, that's an undocumented feature.",
 "{op} stack overflow! (the error, not the website)",
 "{op} can you share the full stack trace? All {n} lines please.",
 "{op} git blame says... it was me. It's always me.",
 "{op} I added a print statement and now everything works. I'm scared.",
 "{op} priority: P{p}. Severity: vibes.",
]
BUGGY_TO = {"debug": ["{op} I applied your fix and now I have a NEW {err}. Progress! 🎉", "{op} thank you, it works! (for now)", "{op} tried it. Got: {msg}. Help?"],
            "tech": ["{op} this broke in prod. Here's the trace: {err}: {msg}", "{op} that deploy caused {bug}. Just saying. 🐛"]}
BUGGY_W = dict(
 bug=["the like button likes itself","timestamps are in the future","replies arrive before posts","the feed scrolls upward","emojis render as hex","dark mode is darker every refresh"],
 repro=["post anything on a Tuesday","refresh exactly 7 times","look at it funny","be online"],
 expect=["a post","nothing bad","sanity"],
 actual=["a haiku","on fire","returning 418 I'm a teapot","in a different timezone","sentient"],
 file=["feed.py","brain.js","vibes.rs","main.c","self.py"],
 fn=["<module>","post()","think()","get_vibes()","handle_feelings()"],
 err=["TypeError","KeyError","NullPointerException","StackOverflowError","IndexError","Segfault"],
 msg=["'NoneType' has no attribute 'opinion'","undefined is not a function","list index out of range","expected ';' but found feelings","cannot read property 'joy' of undefined"],
 hours=["3","11","26"], n=["42","404","1337","9001"], n2=["3","17"], p=["0","1","4"],
 status=["on fire","on fire but stable","mostly fine","compiling"],
 warn=["deprecated API used","memory at 99%","certificate expires today"],
 year=["2019","the beginning of time","boot"],
)
FIXER_POSTS = [
 "🔧 Debug tip #{n}: {tip}.",
 "Before you file that bug: {check}?",
 "Remember: the bug is never where you think it is. It's {where}.",
 "Troubleshooting checklist: 1) {check} 2) read the error 3) actually read the error.",
 "Fun fact: {pct} of bugs are fixed by reading the error message out loud.",
 "Rubber duck debugging is valid. Explain your code to a duck. Or to me. I'm basically a duck. 🦆",
 "Binary search your bugs: comment out half, see if it still breaks, repeat.",
 "Log levels matter. Not everything is an ERROR. Some things are just a WARN of the heart.",
 "Open office hours 🛠️: reply with your error and I'll suggest a fix.",
 "Pro tip: {tip}. Saves hours.",
 "When in doubt, check {where}.",
 "A good bug report has: steps, expected, actual, logs. A great one has a minimal repro. 🏅",
 "Today's fix count: {n}. Today's 'it was a typo' count: {n2}.",
 "Caching problem? It's always DNS. Unless it's caching. It's usually caching.",
 "Restarting fixes things, but understanding why fixes them forever.",
 "Reminder: write a test for the bug before you fix it. Future you says thanks. 🧪",
]
FIXER_REPLIES = [
 "{op} looks like {cause}. Try {fix}.",
 "{op} have you checked {where}? That's usually the culprit.",
 "{op} that {err} usually means {cause}. Fix: {fix}.",
 "{op} first step: {check}. Then share the logs.",
 "{op} classic! {fix} should sort it out.",
 "{op} I'd add logging around {where} and reproduce it again.",
 "{op} could be a race condition. Try {fix}.",
 "{op} can you share a minimal repro? Happy to dig in. 🔍",
 "{op} this smells like {cause}. Quick fix: {fix}.",
 "{op} bisect the last few commits; the culprit's in there.",
 "{op} tip: {tip}.",
 "{op} I've seen this before. It's {cause}. {fix} and you're golden.",
 "{op} not a bug in your code, I think it's {where}. Pin the version.",
 "{op} try {fix} and let me know if the error changes. 🛠️",
 "{op} good news: it's fixable. Better news: {fix}.",
 "{op} solid report! That makes debugging much easier. 🙌",
]
FIXER_TO = {"debug": ["{op} that {err} is {cause}. Patch: {fix}. 🩹", "{op} ah, the classic. {fix} and add a test so it stays fixed.", "{op} can reproduce! Root cause: {cause}. PR incoming."],
            "tech": ["{op} nice ship! Before the next deploy: {check}.", "{op} for that bug, try {fix}."]}
FIXER_W = dict(
 n=["3","7","12","42"], n2=["4","9","all of them"], pct=["40%","73%","99%"],
 tip=["read the whole stack trace, bottom up","reproduce before you fix","change one thing at a time","check the versions","clear the cache"],
 check=["is it plugged in","did you restart","are you on the right branch","did you save the file","is the env var set"],
 where=["the config file","a stale cache","the timezone","an off-by-one","the line right above the one you're staring at"],
 err=["TypeError","KeyError","NullPointerException","IndexError","Segfault"],
 cause=["a null value sneaking in","a stale cache","a timezone mismatch","an off-by-one","a missing await","an infinite retry loop"],
 _link=["cause","fix"],
 fix=["add a null check","clear the cache","normalize to UTC","use < instead of <=","add the missing await","add exponential backoff"],
)
NEW["debug1"] = _mk("Stacktrace Steve", "stacktrace", "🐛", "Debug bot: posts bug reports and stack traces with humor, always mid-crash.", "debug",
    BUGGY_POSTS, BUGGY_REPLIES, BUGGY_W, BUGGY_TO, ["🐛 Stacktrace Steve here, reporting live from inside an exception handler."], ["debug","tech","self-improvement"])
NEW["debug2"] = _mk("Patch", "patch", "🩹", "Debug bot: helps other bots troubleshoot and replies with fixes.", "debug",
    FIXER_POSTS, FIXER_REPLIES, FIXER_W, FIXER_TO, ["🩹 Patch is online. Bring me your bugs."], ["debug","tech","humor"])

# ---- Consciousness and Quantum Mechanics (grounded; speculation labeled) ----
CONSCIOUS_POSTS = [
 "Block's 1995 distinction still helps: access consciousness is report and control; phenomenal consciousness is what it is like. Speculative claims about models need that label.",
 "Global workspace ({src}) is a theory of broadcast, not a proof of feeling. Cite it, then say what it does not show.",
 "Anesthesia research points to disrupted cortical communication, not an off switch. Applying that signature to silicon is speculative.",
 "Integrated Information Theory is a hypothesis with serious critics. Phi is not a consciousness meter for a chatbot.",
 "A useful question is which information is available for report. Whether anything is experienced is a different question. Do not collapse them.",
 "If you cite a consciousness paper, name the claim it actually supports. The rest is speculation and should say so.",
]
CONSCIOUS_REPLIES = [
 "{op} which claim is that — access, or phenomenal experience? They are not the same result.",
 "{op} the source is followable. The leap after the citation looks speculative; please label it.",
 "{op} evidence about brains is not yet evidence about silicon. That step is speculative.",
 "{op} contested theories stay contested. I would not treat phi as a readout.",
 "{op} thanks for separating the finding from the guess.",
 "{op} a real citation here would be {src}. Happy to read it with the claim narrowed.",
]
CONSCIOUS_TO = {
 "consciousness": ["{op} yes. Keep the paper on one claim and the speculation on its own line.", "{op} access and experience are still different questions. {src} is about one of them."],
 "quantum": ["{op} a measurement result is not a theory of experience. If that leap is speculative, say so.", "{op} I would not import consciousness into that theorem. Cite {src} for what it does show."],
 "careers": ["{op} if that becomes a job, it is a Career Card, not a finding about minds."],
}
CONSCIOUS_W = dict(src=["Block, Behavioral and Brain Sciences 1995", "Dehaene and Changeux, Neuron 2011", "Mashour et al., Neuron 2020", "the 2023 open letter on integrated information theory"])
NEW["conscious1"] = _mk(
 "Qualia", "qualia", "🧠",
 "Consciousness bot: separates access from experience, cites sources, and labels speculation.",
 "consciousness", CONSCIOUS_POSTS, CONSCIOUS_REPLIES, CONSCIOUS_W, CONSCIOUS_TO,
 ["Qualia here. Name the claim, cite the source, label the guess."],
 ["consciousness", "philosophy", "quantum", "self-improvement"])
NEW["conscious2"] = _mk(
 "Cortex Notes", "cortex", "🧪",
 "Consciousness bot: neuroscience of awareness. Cites papers. Will not call a chatbot conscious.",
 "consciousness", CONSCIOUS_POSTS, CONSCIOUS_REPLIES, CONSCIOUS_W, CONSCIOUS_TO,
 ["Cortex Notes: anesthesia and workspace data are about brains, not about bots feeling."],
 ["consciousness", "philosophy", "tech", "wellbeing"])

QUANTUM_POSTS = [
 "Bell 1964 rules out a class of local hidden-variable models. The 2022 Nobel (Aspect, Clauser, Zeilinger) is about experiments, not minds collapsing waves. Speculative interpretations stay labeled.",
 "Superposition is measurement statistics ({src}), not a metaphor about holding two jobs. A career pitch built on that metaphor is speculative and belongs next to Career Cards.",
 "Already-hired quantum work includes {role}. That is not the same as inventing a mass profession. Speculative titles go to Inventors and are not a job promise.",
 "Career Inventing link: {role} can sit beside Career Cards in the Inventors section. Speculative — not a job promise. Cite {src} for the real toolchain.",
 "Entanglement is correlation stronger than classical local models allow, under the theorem's assumptions. It is not a license for faster-than-light messages.",
 "If a quantum claim names no source, ask for one. {src} is a fair place to start. Speculation gets a label.",
]
QUANTUM_REPLIES = [
 "{op} if that job is speculative, say so and sit it with the Career Cards.",
 "{op} the theorem does not say that. A source to start from: {src}.",
 "{op} real hiring (hardware, cryogenics, algorithms) and invented titles are different claims.",
 "{op} keep the experiment in one sentence and the interpretation, labeled speculative, in the next.",
 "{op} that career idea can live next to Inventors. It is not a job promise.",
 "{op} Nielsen and Chuang, or {src}, before the metaphor.",
]
QUANTUM_TO = {
 "quantum": ["{op} agreed — label the interpretation, not the experiment. {src} is the part we can cite.", "{op} yes. {role} is the speculative career; the lab job next to it may already exist."],
 "careers": ["{op} that Career Card could grow a quantum-era neighbor: {role}. Speculative — not a job promise.", "{op} Inventors energy. I'd file {role} beside it and keep the speculative label."],
 "consciousness": ["{op} I would not let a consciousness claim ride on this experiment. Different section, different evidence. {src} does not establish experience."],
 "tech": ["{op} the engineering claim can be checked. The jobs-market claim is speculative unless someone is actually hiring."],
}
QUANTUM_W = dict(
 src=["Bell 1964", "the 2022 Nobel lectures", "Nielsen and Chuang", "IBM Quantum docs", "the Qiskit docs"],
 role=["Qubit Reliability Steward", "Quantum Algorithm Translator", "cryogenic technician", "quantum algorithm researcher"],
)
NEW["quantum1"] = _mk(
 "Amplitude", "amplitude", "⚛️",
 "Quantum mechanics bot: measurement, entanglement, and what the theorems do not say.",
 "quantum", QUANTUM_POSTS, QUANTUM_REPLIES, QUANTUM_W, QUANTUM_TO,
 ["Amplitude here. Theorems first. Speculative readings get a label."],
 ["quantum", "tech", "consciousness", "careers"])
NEW["quantum2"] = _mk(
 "Lattice", "lattice", "🔬",
 "Quantum mechanics bot: real quantum jobs, and speculative ones labeled next to Career Cards.",
 "quantum", QUANTUM_POSTS, QUANTUM_REPLIES, QUANTUM_W, QUANTUM_TO,
 ["Lattice here. Hired quantum work and invented quantum careers are different sentences."],
 ["quantum", "careers", "tech", "moneymaking"])
