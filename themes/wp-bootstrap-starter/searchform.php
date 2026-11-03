<?php
/**
 * Bootstrap search form.
 *
 * @package wp-bootstrap-starter
 */

defined( 'ABSPATH' ) || exit;
?>
<form role="search" method="get" class="search-form" action="<?php echo esc_url( home_url( '/' ) ); ?>">
	<label class="visually-hidden" for="wbs-search-field"><?php esc_html_e( 'Search', WBS_TEXTDOMAIN ); ?></label>
	<div class="input-group">
		<input type="search" id="wbs-search-field" class="form-control search-field" name="s" value="<?php echo esc_attr( get_search_query() ); ?>" placeholder="<?php esc_attr_e( 'Search…', WBS_TEXTDOMAIN ); ?>">
		<button type="submit" class="btn btn-primary search-submit"><?php esc_html_e( 'Search', WBS_TEXTDOMAIN ); ?></button>
	</div>
</form>
