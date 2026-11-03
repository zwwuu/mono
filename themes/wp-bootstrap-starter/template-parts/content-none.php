<?php
/**
 * "Nothing found" template part.
 *
 * @package wp-bootstrap-starter
 */

defined( 'ABSPATH' ) || exit;
?>
<section class="no-results not-found">
	<header class="mb-3">
		<h1 class="h4"><?php esc_html_e( 'Nothing found', WBS_TEXTDOMAIN ); ?></h1>
	</header>
	<div class="alert alert-info">
		<?php esc_html_e( 'Sorry, but nothing matched your criteria. Try a different search.', WBS_TEXTDOMAIN ); ?>
	</div>
	<?php get_search_form(); ?>
</section>
