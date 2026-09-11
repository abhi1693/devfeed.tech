# DevFeed

**Find your next worthwhile read.**

Keeping up with software means sifting through news sites, blogs and community
links. DevFeed is being built to bring developer news, tutorials and articles into
one place, organized around the topics you care about.

Find something useful, get a quick preview, and head to the original publisher
to read it.

**DevFeed is in early development.** The public user, feed and editorial
tools are working. Browser extensions are planned. Follow along and help shape
the reading experience.

## A feed that fits your curiosity

- **Less searching, more reading.** Bring articles from different publications
  and communities together in one feed, with the newest items first.
- **Choose what interests you.** Explore topics, search for something specific,
  or narrow your reading by language and content type—from tutorials to release
  news. Browse a topic or a publication to find your next read.
- **Know where a story comes from.** See a preview and its source before opening
  the original article. DevFeed points you to the publisher for the full read.
- **Make room for relevant content.** Sources and articles go through review
  before appearing in the public feed.
- **Read without an account.** Discovery is designed around your interests,
  with no user registration required.

The anonymous user includes a responsive card feed, search, topic and source
directories, and article previews. Run it at `http://localhost:3000` with
[Docker Compose](docs/compose.md). The API also supports excluding tags and sources;
browser extensions are planned.

## Help shape DevFeed

Have a publication you always return to, a topic that's hard to follow, or an
idea that would make your daily reading better? We'd like to hear it.

- [Suggest a publication or feed](https://github.com/abhi1693/devfeed.tech/issues/new?template=03_source_suggestion.yml).
- [Suggest an improvement](https://github.com/abhi1693/devfeed.tech/issues/new?template=02_feature_request.yml) or [join the discussion](https://github.com/abhi1693/devfeed.tech/discussions).
- Star this repository to keep DevFeed close, and watch it for project updates.

## Want to build with us?

Want to run your own instance? Follow the [Docker Compose setup](docs/compose.md).

Start with the [development guide](docs/development.md). Setup, architecture,
administration and release details live there, alongside links to the full
technical documentation.

Optional user sign-in, followed topics, and My feed are described in [user accounts](docs/user-accounts.md). Public browsing remains anonymous.

Production rollout and service boundaries are documented in [Kubernetes deployment](docs/kubernetes.md).
