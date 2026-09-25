# Premise

We are developing a novel AI agent to empower small to mid-size business #owners to control their web presence.

There are three components:

- #businessdata is the business data that the #owner maintains.  The schema of #businessdata is fixed, but the content can be freely modified by the #owner.  Part of #businessdata contains resources such as documents, images, and other types of raw files.

- #design is the web design of the web site.  This is a set of templates that can be used to render the #businessdata.  The #design can be modified by the #owner.  The #design can contain assets such as images and audio files.

- #site is the final generated web content from #businessdata and #design.  The #owner cannot directly modify the #site content, but rather, can control the deployment of the #site once they verify the accuracy of #businessdata and the look-and-feel of #design.

# Workflow for #owners

We assume #owners all have AI agents such as (codex and claude) and they will only interact with our service through MCP.  Thus, our service will be bundled under one or more MCP servers.  Through the services exposed by the MCP server, the owners can query and update the #businessdata, modify the #design, and deploy their #site live.

# Technology stack

- Backend services are to be implemented in Python using `uv` tools.
- Web design is limited to client-side static content (with Javascript), expressed as a directory of jinja2 template files and assets.
- The site is a directory of HTML, javascript and files (assets or resources).

