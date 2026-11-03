<?php
/**
 * Intranet mode: disable all internet access.
 *
 * When WBS_INTRANET_MODE is true (the default), this file:
 *
 *  1. Blocks every outbound HTTP request made through the WP HTTP API
 *     unless the host is localhost, a private (RFC 1918 / link-local)
 *     address, or explicitly allow-listed via the 'wbs_allowed_hosts'
 *     filter (or WP_ACCESSIBLE_HOSTS in wp-config.php).
 *  2. Neuters update checks and auto-updates (they can't reach
 *     wordpress.org anyway, so fail them fast instead of timing out).
 *  3. Disables pingbacks/trackbacks and removes their headers.
 *  4. Removes external-client discovery links (RSD, wlwmanifest),
 *     oEmbed discovery links, and the WP version meta tag.
 *  5. Serves local SVG avatars instead of Gravatar (remote).
 *
 * Recommended wp-config.php additions (belt and suspenders, see README):
 *   define( 'WP_HTTP_BLOCK_EXTERNAL', true );
 *   define( 'WP_ACCESSIBLE_HOSTS', '' );
 *   define( 'AUTOMATIC_UPDATER_DISABLED', true );
 *
 * Limit: this blocks the WP HTTP API. Code that bypasses it with raw
 * sockets (fsockopen, curl_*) is outside WordPress's control — use
 * firewall rules for that layer.
 *
 * @package wp-bootstrap-starter
 */

defined( 'ABSPATH' ) || exit;

if ( ! defined( 'WBS_INTRANET_MODE' ) || ! WBS_INTRANET_MODE ) {
	return;
}

/**
 * Hosts that may still be reached.
 *
 * Merges the 'wbs_allowed_hosts' filter with WP_ACCESSIBLE_HOSTS.
 * Example (child theme or plugin):
 *   add_filter( 'wbs_allowed_hosts', fn( $hosts ) => array_merge( $hosts, [ 'updates.internal.example' ] ) );
 *
 * @return string[] Lowercase hostnames/IPs.
 */
function wbs_allowed_hosts() {
	$hosts = (array) apply_filters( 'wbs_allowed_hosts', array() );

	if ( defined( 'WP_ACCESSIBLE_HOSTS' ) ) {
		foreach ( explode( ',', WP_ACCESSIBLE_HOSTS ) as $host ) {
			$host = strtolower( trim( $host ) );
			if ( '' !== $host ) {
				$hosts[] = $host;
			}
		}
	}

	return array_unique( $hosts );
}

/**
 * Whether a host is local (intranet-safe).
 *
 * @param string $host Hostname or IP from the request URL.
 * @return bool
 */
function wbs_is_local_host( $host ) {
	$host = strtolower( trim( (string) $host, " \t\n\r\0\x0B[]" ) );

	if ( in_array( $host, array( 'localhost', '127.0.0.1', '::1' ), true ) ) {
		return true;
	}
	if ( in_array( $host, wbs_allowed_hosts(), true ) ) {
		return true;
	}
	if ( filter_var( $host, FILTER_VALIDATE_IP, FILTER_FLAG_IPV4 ) ) {
		$long   = ip2long( $host );
		$ranges = array(
			array( '10.0.0.0', '10.255.255.255' ),       // Private.
			array( '172.16.0.0', '172.31.255.255' ),     // Private.
			array( '192.168.0.0', '192.168.255.255' ),   // Private.
			array( '169.254.0.0', '169.254.255.255' ),   // Link-local.
		);
		foreach ( $ranges as $range ) {
			if ( $long >= ip2long( $range[0] ) && $long <= ip2long( $range[1] ) ) {
				return true;
			}
		}
	}

	return false;
}

add_filter( 'pre_http_request', 'wbs_block_external_http', 10, 3 );
/**
 * Short-circuit any outbound request to a non-local host.
 *
 * @param false|array|WP_Error $preempt Whether to preempt the request.
 * @param array                $args    Request arguments.
 * @param string               $url     Request URL.
 * @return false|array|WP_Error
 */
function wbs_block_external_http( $preempt, $args, $url ) {
	if ( false !== $preempt ) {
		return $preempt; // Another filter already handled it.
	}

	$host = parse_url( $url, PHP_URL_HOST );
	if ( ! $host || ! wbs_is_local_host( $host ) ) {
		return new WP_Error(
			'wbs_external_blocked',
			sprintf(
				/* translators: %s: blocked URL. */
				__( 'External HTTP requests are disabled (intranet mode): %s', WBS_TEXTDOMAIN ),
				$url
			)
		);
	}

	return false;
}

// --- Update checks & auto-updates: fail fast instead of timing out. ---
add_filter( 'pre_site_transient_update_core', '__return_null' );
add_filter( 'pre_site_transient_update_plugins', '__return_null' );
add_filter( 'pre_site_transient_update_themes', '__return_null' );
add_filter( 'automatic_updater_disabled', '__return_true' );
add_filter( 'auto_update_core', '__return_false' );
add_filter( 'auto_update_plugin', '__return_false' );
add_filter( 'auto_update_theme', '__return_false' );

// --- Pingbacks / trackbacks off. ---
add_filter( 'xmlrpc_methods', 'wbs_disable_pingback_methods' );
/**
 * Remove pingback methods from XML-RPC.
 *
 * @param array $methods Registered methods.
 * @return array
 */
function wbs_disable_pingback_methods( $methods ) {
	unset( $methods['pingback.ping'], $methods['pingback.extensions.getPingbacks'] );
	return $methods;
}

add_filter( 'wp_headers', 'wbs_remove_pingback_header' );
/**
 * Remove the X-Pingback response header.
 *
 * @param array $headers Response headers.
 * @return array
 */
function wbs_remove_pingback_header( $headers ) {
	unset( $headers['X-Pingback'] );
	return $headers;
}

// --- Fewer discovery / fingerprinting links in <head>. ---
remove_action( 'wp_head', 'rsd_link' );
remove_action( 'wp_head', 'wlwmanifest_link' );
remove_action( 'wp_head', 'wp_oembed_add_discovery_links' );
remove_action( 'wp_head', 'wp_generator' );

add_filter( 'get_avatar', 'wbs_local_avatar', 10, 5 );
/**
 * Replace Gravatar (remote) with a local inline-SVG initial avatar.
 * Keeps comments looking right with zero internet access.
 *
 * @param string                 $avatar      Avatar HTML.
 * @param mixed                  $id_or_email User identifier.
 * @param int                    $size        Avatar size in px.
 * @param string                 $default     Unused (Gravatar default).
 * @param string                 $alt         Alt text.
 * @return string
 */
function wbs_local_avatar( $avatar, $id_or_email, $size, $default, $alt ) {
	$name = '';
	if ( is_object( $id_or_email ) && isset( $id_or_email->comment_author ) ) {
		$name = $id_or_email->comment_author;
	} elseif ( is_numeric( $id_or_email ) ) {
		$user = get_user_by( 'id', (int) $id_or_email );
		$name = $user ? $user->display_name : '';
	} elseif ( is_string( $id_or_email ) ) {
		$user = get_user_by( 'email', $id_or_email );
		$name = $user ? $user->display_name : $id_or_email;
	}

	$initial = '' !== trim( $name ) ? mb_strtoupper( mb_substr( trim( $name ), 0, 1 ) ) : '?';
	$svg     = '<svg xmlns="http://www.w3.org/2000/svg" width="' . (int) $size . '" height="' . (int) $size . '" viewBox="0 0 100 100">'
		. '<rect width="100" height="100" rx="50" fill="#6c757d"/>'
		. '<text x="50" y="68" font-size="52" text-anchor="middle" fill="#fff" font-family="system-ui,sans-serif">' . esc_html( $initial ) . '</text>'
		. '</svg>';

	return sprintf(
		'<img alt="%s" src="%s" class="avatar avatar-%d photo rounded-circle" height="%d" width="%d" loading="lazy" decoding="async" />',
		esc_attr( $alt ),
		esc_url( 'data:image/svg+xml;base64,' . base64_encode( $svg ) ),
		(int) $size,
		(int) $size,
		(int) $size
	);
}
