# WP Bootstrap Starter

A simple, extensible WordPress starter theme built on **locally-hosted
Bootstrap 5.3.3**. No Node, no Composer, no build step — drop it into
`wp-content/themes/` and activate. Designed to run fully offline on an
intranet: it makes **zero internet requests**.

## Install

1. Copy the `wp-bootstrap-starter` folder to `wp-content/themes/`.
2. In wp-admin go to **Appearance → Themes** and activate.
3. Assign menus under **Appearance → Menus** (Primary, Footer).

No `npm install`, no `composer install`, nothing to compile.

## What's included

| File / dir | Purpose |
|---|---|
| `functions.php` | Manifest — just requires the `/inc` modules. |
| `inc/config.php` | Constants: versions, paths, `WBS_INTRANET_MODE` toggle. |
| `inc/setup.php` | Theme supports (title-tag, thumbnails, html5, custom-logo, …), nav menus, image sizes. |
| `inc/enqueue.php` | Loads Bootstrap CSS/JS and `style.css` from `/assets` — no CDN, no webfonts (Bootstrap uses the system font stack). |
| `inc/nav-walker.php` | Minimal Bootstrap 5 dropdown menu walker. |
| `inc/template-tags.php` | Post meta, Bootstrap pagination, comment markup, page-based menu fallback. |
| `inc/customizer.php` | Theme options: layout width, navbar scheme, footer text. |
| `inc/intranet.php` | Intranet mode — blocks all internet access (see below). |
| `assets/` | Vendored Bootstrap 5.3.3 CSS + JS bundle. |
| `template-parts/` | Reusable loop/content partials. |

## Intranet mode (internet access disabled)

`WBS_INTRANET_MODE` is **on by default**. While on, the theme:

1. **Blocks outbound HTTP** through the WP HTTP API to anything that is
   not `localhost`, a private (RFC 1918 / link-local) address, or a host
   you allow-list. Update checks, plugin API calls, news feeds, Gravatar —
   all fail fast instead of timing out.
2. **Disables update checks and auto-updates** (core/plugins/themes).
3. **Disables pingbacks/trackbacks** and removes the `X-Pingback` header.
4. **Removes** RSD/wlwmanifest links, oEmbed discovery links, and the
   WP version meta tag from `<head>`.
5. **Replaces Gravatar with local inline-SVG avatars** so comments look
   right offline.

### Recommended wp-config.php additions

```php
define( 'WP_HTTP_BLOCK_EXTERNAL', true );
define( 'WP_ACCESSIBLE_HOSTS', '' );   // '' = localhost only
define( 'AUTOMATIC_UPDATER_DISABLED', true );
define( 'WP_AUTO_UPDATE_CORE', false );
define( 'DISALLOW_FILE_EDIT', true );  // harden the admin on shared intranets
```

### Allow-listing an internal host

Some intranets legitimately need a local service (e.g. an internal update
mirror). In a child theme or small plugin:

```php
add_filter( 'wbs_allowed_hosts', function ( $hosts ) {
    return array_merge( $hosts, [ 'updates.internal.example' ] );
} );
```

### Turning intranet mode off

```php
define( 'WBS_INTRANET_MODE', false ); // in wp-config.php, before the theme loads
```

Note: the block covers the WP HTTP API. Code that bypasses it with raw
sockets (`fsockopen`, `curl_*`) is outside WordPress's control — cover that
layer with firewall rules. Since updates are disabled, apply WordPress,
plugin, and theme updates manually from the server side.

## Extensibility

**Custom action hooks** you can tap into from a child theme or plugin:

- `wbs_before_header`, `wbs_after_header`
- `wbs_before_main`, `wbs_after_main`
- `wbs_before_footer`, `wbs_after_footer`
- `wbs_before_loop`, `wbs_after_loop`
- `wbs_entry_top`, `wbs_entry_bottom` (inside single post markup)

**Filters:**

- `wbs_allowed_hosts` — extra hosts permitted in intranet mode.

**Config constants** (define before the theme loads to override):

- `WBS_INTRANET_MODE`, `WBS_VERSION`, `WBS_BOOTSTRAP_VERSION`

Standard WordPress hooks (`wp_head`, `wp_footer`, `the_content`, …) work
as usual.

## Upgrading Bootstrap later

Replace the two files in `assets/css` and `assets/js` with the new
release and bump `WBS_BOOTSTRAP_VERSION` in `inc/config.php`. That's it —
still no build step.
