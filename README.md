**Resilient LLM Gateway - Team 2 - Pace University Capstone Project**

**Project Description**
A production-grade, unified REST API middleware designed to sit in front of multiple Large Language Models (LLMs) such as OpenAI and Anthropic. The gateway provides a resilient infrastructure for applications by handling automatic failover, semantic caching via Redis to reduce costs, and granular token usage tracking in PostgreSQL. This project focuses on backend engineering challenges like smart routing and reliability, ensuring seamless model switching without client-side code changes.

**Team Members**
**Team Members**
<table style="width:100%" border="0" cellspacing="0" cellpadding="0">
  <tr>
    <td align="center" valign="top" width="33%">
      <img src="./docs/Nisarga.jpeg" width="200"><br />
      <b>Nisarga Vishwamanjuswamy</b><br />
      <i>Project Manager & Developer</i><br />
      (nv86609n@pace.edu)
    </td>
    <td align="center" valign="top" width="33%">
      <img src="./docs/Prachi.jpeg" width="200"><br />
      <b>Prachi Budhrani</b><br />
      <i>Data & Observability Engineer & Developer</i><br />
      (pb78229n@pace.edu)
    </td>
    <td align="center" valign="top" width="33%">
      <img src="./docs/Diya.jpeg" width="200"><br />
      <b>Diya Farakte</b><br />
      <i>Business Analyst & Developer</i><br />
      (df13729n@pace.edu)
    </td>
  </tr>
  <tr>
    <td align="center" valign="top">
      <img src="https://github.com/identicons/Pramodh.png" width="200"><br />
      <b>Pramod Kumar Reddy Parvath Reddy</b><br />
      <i>LLM Integration Engineer & Developer</i><br />
      (pp17587n@pace.edu)
    </td>
    <td align="center" valign="top">
      <img src="./docs/Rohan.jpeg" width="200"><br />
      <b>Rohan Brahmbhatt</b><br />
      <i>Performance Engineer & Developer</i><br />
      (rb28301n@pace.edu)
    </td>
    <td align="center" valign="top"></td>
  </tr>
</table>

**Project Design**
Our implementation follows a modular middleware architecture using an Adapter Pattern for different LLM providers. Requests are authenticated via API keys, checked against rate limits in Redis, and then routed based on prompt complexity or provider health.


**Languages and Tools**

<p align="left"> <img src="https://cdn.jsdelivr.net/gh/devicons/devicon/icons/python/python-original.svg" title="Python" alt="Python" width="60" height="60"/>&nbsp; <img src="https://cdn.jsdelivr.net/gh/devicons/devicon/icons/fastapi/fastapi-original.svg" title="FastAPI" alt="FastAPI" width="60" height="60"/>&nbsp; <img src="https://cdn.jsdelivr.net/gh/devicons/devicon/icons/postgresql/postgresql-original.svg" title="PostgreSQL" alt="PostgreSQL" width="60" height="60"/>&nbsp; <img src="https://cdn.jsdelivr.net/gh/devicons/devicon/icons/redis/redis-original.svg" title="Redis" alt="Redis" width="60" height="60"/>&nbsp; <img src="https://cdn.jsdelivr.net/gh/devicons/devicon/icons/docker/docker-original.svg" title="Docker" alt="Docker" width="60" height="60"/>&nbsp; <img src="https://cdn.jsdelivr.net/gh/devicons/devicon/icons/github/github-original.svg" title="GitHub" alt="GitHub" width="60" height="60"/>&nbsp; <img src="https://cdn.jsdelivr.net/gh/devicons/devicon/icons/sqlalchemy/sqlalchemy-original.svg" title="SQLAlchemy" alt="SQLAlchemy" width="60" height="60"/>&nbsp; </p>


**CS691 - Spring 2026 Deliverables
Presentations (Sprint Reviews)**

Watch Deliverable 1 Presentation Video (Sprint 0) <br />1a. View Deliverable 1 Presentation Slides as PDF <br />1b. <a id="raw-url" href="./docs/sprint0_slides.pptx">Download Deliverable 1 Presentation Slides as PowerPoint</a>

Sprint Burndown Charts and Completed Tasks

Deliverable 1 Completed Tasks (Sprint 0)

Team Working Agreement

Team Working Agreement as PDF | <a id="raw-url" href="./docs/team_agreement.docx">Download Team Working Agreement as Word Document</a>

**Additional Project Artifacts**
**Product Personas**

Persona 1 - The Cost-Conscious Developer


Persona 2 - The Enterprise Architect


Persona 3 - The AI Startup Founder
