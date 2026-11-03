<?php
/**
 * 404 — page not found.
 *
 * @package wp-bootstrap-starter
 */

defined( 'ABSPATH' ) || exit;

get_header();
?>
<div class="row justify-content-center">
	<div class="col-lg-8 text-center py-5">
		<h1 class="display-1 fw-bold">404</h1>
		<p class="lead"><?php esc_html_e( 'The page you are looking for could not be found.', WBS_TEXTDOMAIN ); ?></p>
		<div class="d-flex justify-content-center gap-2 mt-4">
			<a class="btn btn-primary" href="<?php echo esc_url( home_url( '/' ) ); ?>"><?php esc_html_e( 'Back to home', WBS_TEXTDOMAIN ); ?></a>
		</div>
		<div class="mt-4 d-flex justify-content-center">
			<?php get_search_form(); ?>
		</div>
	</div>
</div>
<?php
get_footer();
