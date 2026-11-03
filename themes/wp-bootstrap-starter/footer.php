<?php
/**
 * Site footer: closing layout wrappers, footer widgets, credits.
 *
 * Extensibility hooks: 'wbs_after_main', 'wbs_before_footer', 'wbs_after_footer'.
 *
 * @package wp-bootstrap-starter
 */

defined( 'ABSPATH' ) || exit;

$footer_text = trim( (string) get_theme_mod( 'wbs_footer_text', '' ) );
if ( '' === $footer_text ) {
	$footer_text = sprintf(
		/* translators: 1: year, 2: site name. */
		__( '&copy; %1$s %2$s. All rights reserved.', WBS_TEXTDOMAIN ),
		date_i18n( 'Y' ),
		get_bloginfo( 'name' )
	);
}
?>
	</div><!-- .container / .container-fluid -->

	<?php do_action( 'wbs_after_main' ); ?>
</main>

<?php do_action( 'wbs_before_footer' ); ?>

<footer class="site-footer bg-dark text-light mt-auto py-4">
	<div class="<?php echo esc_attr( get_theme_mod( 'wbs_container', 'container' ) ); ?>">
		<?php
		wp_nav_menu(
			array(
				'theme_location' => 'footer',
				'container'      => false,
				'menu_class'     => 'nav justify-content-center mb-3',
				'fallback_cb'    => '__return_false',
				'depth'          => 1,
			)
		);
		?>

		<p class="text-center mb-0 small"><?php echo wp_kses_post( $footer_text ); ?></p>
	</div>
</footer>

<?php do_action( 'wbs_after_footer' ); ?>

<?php wp_footer(); ?>
</body>
</html>
