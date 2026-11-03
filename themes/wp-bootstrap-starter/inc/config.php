<?php
/**
 * Theme configuration: constants and global toggles.
 *
 * Override any of these from wp-config.php or a child theme's
 * functions.php BEFORE this theme loads (they are only defined
 * if not already defined).
 *
 * @package wp-bootstrap-starter
 */

defined( 'ABSPATH' ) || exit;

if ( ! defined( 'WBS_VERSION' ) ) {
	define( 'WBS_VERSION', '1.0.0' );
}

if ( ! defined( 'WBS_BOOTSTRAP_VERSION' ) ) {
	define( 'WBS_BOOTSTRAP_VERSION', '5.3.3' ); // Vendored in /assets.
}

if ( ! defined( 'WBS_TEXTDOMAIN' ) ) {
	define( 'WBS_TEXTDOMAIN', 'wp-bootstrap-starter' );
}

if ( ! defined( 'WBS_DIR' ) ) {
	define( 'WBS_DIR', get_template_directory() );
}

if ( ! defined( 'WBS_URI' ) ) {
	define( 'WBS_URI', get_template_directory_uri() );
}

if ( ! defined( 'WBS_ASSETS_URI' ) ) {
	define( 'WBS_ASSETS_URI', WBS_URI . '/assets' );
}

/**
 * Intranet mode: block ALL outbound internet access from WordPress
 * (updates, Gravatar, pingbacks, oEmbed discovery, any plugin's
 * remote API calls via the WP HTTP API).
 *
 * Localhost, private (RFC 1918 / link-local) addresses, and hosts
 * added via the 'wbs_allowed_hosts' filter still work.
 *
 * Set to false in wp-config.php if you ever take the site online:
 *   define( 'WBS_INTRANET_MODE', false );
 */
if ( ! defined( 'WBS_INTRANET_MODE' ) ) {
	define( 'WBS_INTRANET_MODE', true );
}
